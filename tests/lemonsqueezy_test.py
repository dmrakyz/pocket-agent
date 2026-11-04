"""Lemon Squeezy integration against a fake API."""
import asyncio, json, os, sys, tempfile
os.environ["APP_PASSWORD"] = "x"; os.environ["DATA_DIR"] = tempfile.mkdtemp(); os.environ["LEMONSQUEEZY_API_KEY"] = "lskey"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import httpx
from app import lemonsqueezy as L, perms, tools

LOG = []
def handler(req):
    path = req.url.path; q = dict(req.url.params); LOG.append((req.method, path, q, req.headers.get("authorization"), req.headers.get("accept"), json.loads(req.content) if req.content else None))
    d = lambda items: httpx.Response(200, json={"data": items})
    if path == "/v1/stores": return d([{"id": "3", "attributes": {"name": "Ehe Templates", "currency": "USD"}}])
    if path == "/v1/stores/3": return d({"id": "3", "attributes": {"name": "Ehe Templates", "currency": "USD", "total_sales": 4, "total_revenue": 5600, "thirty_day_sales": 2, "thirty_day_revenue": 2800, "url": "https://x.lemonsqueezy.com"}})
    if path == "/v1/users/me": return d({"id": "1", "attributes": {"test_mode": True}})
    if path == "/v1/products": return d([{"id": "10", "attributes": {"name": "Freelancer P&L", "status": "published", "price_formatted": "$14.00", "buy_now_url": "https://x.lemonsqueezy.com/buy/abc"}}])
    if path == "/v1/variants": return d([{"id": "20", "attributes": {"product_id": 10, "name": "Default", "status": "published", "price": 1400}}])
    if path == "/v1/orders": return d([{"id": "30", "attributes": {"order_number": 1001, "status": "paid", "refunded": False, "total_formatted": "$14.00", "user_email": "a@b.co", "created_at": "2026-11-05T10:00:00Z", "first_order_item": {"product_name": "Freelancer P&L"}}}])
    if path == "/v1/checkouts" and req.method == "POST": return httpx.Response(201, json={"data": {"id": "c", "attributes": {"url": "https://x.lemonsqueezy.com/checkout/custom/zzz"}}})
    if path == "/v1/orders/30/refund": return d({"id": "30", "attributes": {"status": "refunded"}})
    if path == "/v1/orders/99/refund": return httpx.Response(422, json={"errors": [{"title": "Unprocessable", "detail": "already refunded"}]})
    return httpx.Response(404, json={"errors": [{"title": "Not found", "detail": path}]})
L._transport = httpx.MockTransport(handler)

async def main():
    st = await L.status(); assert st["store"] == "Ehe Templates (USD)" and st["mode"] == "test", st
    ctx = object()
    assert "total sales 4" in await tools.t_ls_read(ctx, {"what": "store"})
    assert "buy/abc" in await tools.t_ls_read(ctx, {"what": "products"})
    assert "variant 20" in await tools.t_ls_read(ctx, {"what": "variants", "product_id": "10"})
    assert "order 30 #1001 paid" in await tools.t_ls_read(ctx, {"what": "orders"})
    assert LOG[-1][2].get("filter[store_id]") == "3" and LOG[-1][3] == "Bearer lskey" and "vnd.api+json" in LOG[-1][4]
    url = await tools.t_ls_checkout(ctx, {"variant_id": "20", "email": "a@b.co"})
    assert url.endswith("/zzz")
    body = [x for x in LOG if x[0] == "POST"][0][5]
    assert body["data"]["relationships"]["variant"]["data"] == {"type": "variants", "id": "20"} and body["data"]["attributes"]["checkout_data"]["email"] == "a@b.co"
    assert "refunded" in await tools.t_ls_refund(ctx, {"order_id": "30"})
    try: await tools.t_ls_refund(ctx, {"order_id": "99"}); assert False
    except tools.ToolError as e: assert "already refunded" in str(e)
    try: await tools.t_ls_read(ctx, {"what": "nope"}); assert False
    except tools.ToolError: pass
    assert perms.decide({"mode": "yolo", "allow": []}, "ls_refund", {})[0] == "ask" and perms.always_rule("ls_refund", {}) is None
    assert perms.decide({"mode": "plan"}, "ls_checkout", {})[0] == "deny" and perms.decide({"mode": "ask", "allow": []}, "ls_checkout", {})[0] == "allow"
    assert "ls_read" in tools.READONLY and "ls_refund" not in tools.READONLY
    names = {t["function"]["name"] for t in tools.schemas({"mode": "ask"}, None)}
    assert {"ls_read", "ls_checkout", "ls_refund"} <= names
    print("lemonsqueezy OK")
asyncio.run(main())
