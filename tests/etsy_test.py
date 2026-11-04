"""Etsy integration against a fake Etsy (httpx MockTransport)."""
import asyncio, json, os, sys, tempfile, time
from pathlib import Path
from urllib.parse import parse_qs, urlparse
os.environ["APP_PASSWORD"] = "x"; os.environ["DATA_DIR"] = tempfile.mkdtemp(); os.environ["ETSY_API_KEY"] = "kstring"; os.environ["ETSY_SHARED_SECRET"] = "sek"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import httpx
from app import etsy, kv, perms, tools

LOG = []
STATE = {"expires_in": 3600, "listings": {}}

def handler(req: httpx.Request) -> httpx.Response:
    u = urlparse(str(req.url)); body = req.content.decode("latin-1") if req.content else ""
    LOG.append((req.method, u.path, req.headers.get("x-api-key"), req.headers.get("authorization"), body[:400]))
    if u.path.endswith("/oauth/token"):
        f = parse_qs(body)
        if f["grant_type"] == ["authorization_code"]:
            assert f["code"] == ["thecode"] and f["code_verifier"] and f["client_id"] == ["kstring"], f
            return httpx.Response(200, json={"access_token": "777.abc", "refresh_token": "r1", "expires_in": STATE["expires_in"]})
        assert f["grant_type"] == ["refresh_token"] and f["refresh_token"] == ["r1"]
        return httpx.Response(200, json={"access_token": "777.def", "refresh_token": "r2", "expires_in": 3600})
    if u.path.endswith("/users/777/shops"):
        return httpx.Response(200, json={"shop_id": 42, "shop_name": "FinTemplates"})
    if u.path.endswith("/shops/42") and req.method == "GET":
        return httpx.Response(200, json={"shop_name": "FinTemplates", "listing_active_count": 1, "transaction_sold_count": 3, "review_count": 2, "review_average": 5, "currency_code": "USD"})
    if u.path.endswith("/shops/42/listings") and req.method == "POST":
        STATE["listings"][99] = {"state": "draft"}
        assert "type=download" in body and "tags=" in body, body
        return httpx.Response(201, json={"listing_id": 99, "state": "draft"})
    if u.path.endswith("/shops/42/listings") and req.method == "GET":
        return httpx.Response(200, json={"results": [{"listing_id": 99, "state": "active", "title": "Freelancer P&L", "price": {"amount": 1500, "divisor": 100, "currency_code": "USD"}, "views": 10, "num_favorers": 2}]})
    if u.path.endswith("/listings/99/files") or u.path.endswith("/listings/99/images"):
        assert b"filename=" in req.content
        return httpx.Response(201, json={"ok": 1})
    if u.path.endswith("/shops/42/listings/99") and req.method == "PATCH":
        if "boom" in body: return httpx.Response(400, json={"error": "title too long"})
        return httpx.Response(200, json={"listing_id": 99})
    if u.path.endswith("/shops/42/receipts"):
        return httpx.Response(200, json={"results": [{"receipt_id": 5, "is_paid": True, "is_shipped": False, "grandtotal": {"amount": 1500, "divisor": 100, "currency_code": "USD"}, "transactions": [{"title": "Freelancer P&L"}]}]})
    if u.path.endswith("/seller-taxonomy/nodes"):
        return httpx.Response(200, json={"results": [{"id": 1, "name": "Paper & Party", "children": [{"id": 2, "name": "Planners & Calendars", "children": []}]}]})
    return httpx.Response(404, json={"error": "nope " + u.path})
etsy._transport = httpx.MockTransport(handler)

async def main():
    assert etsy.configured() and not etsy.connected()
    try: await etsy.read("shop"); assert False
    except etsy.EtsyError as e: assert "not connected" in str(e)
    url = etsy.start("https://agent.example.com")
    q = parse_qs(urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"] and q["redirect_uri"] == ["https://agent.example.com/etsy/callback"] and "listings_w" in q["scope"][0]
    try: etsy.start("http://evil.com"); assert False
    except etsy.EtsyError: pass
    try: await etsy.finish("bad-state", "thecode"); assert False
    except etsy.EtsyError: pass
    name = await etsy.finish(q["state"][0], "thecode")
    assert name == "FinTemplates" and etsy.connected() and etsy.status()["shop_id"] == 42
    assert any(r[2] == "kstring:sek" and r[3] == "Bearer 777.abc" for r in LOG if r[1].endswith("/shops"))

    root = Path(tempfile.mkdtemp()); (root / "t.pdf").write_bytes(b"%PDF fake"); (root / "a.png").write_bytes(b"\x89PNG")
    ctx = type("C", (), {"root": root, "resolve": lambda self, p, must_exist=False: root / p})()
    assert "FinTemplates" in await tools.t_etsy_read(ctx, {"what": "shop"})
    assert "Freelancer P&L" in await tools.t_etsy_read(ctx, {"what": "listings"}) and "15.00 USD" in await tools.t_etsy_read(ctx, {"what": "listings"})
    assert "#5 paid" in await tools.t_etsy_read(ctx, {"what": "orders"})
    assert "2: Paper & Party > Planners & Calendars" in await tools.t_etsy_read(ctx, {"what": "taxonomy", "query": "planners"})
    ctx2 = type("C", (), {"root": root, "resolve": staticmethod(lambda p, must_exist=False: root / p)})()
    r = await tools.t_etsy_draft(ctx2, {"title": "Freelancer P&L", "description": "x" * 50, "price": 15, "taxonomy_id": 2, "tags": ["budget", "budget", "freelance"] + ["t%d" % i for i in range(20)], "file": "t.pdf", "images": ["a.png"]})
    assert "Draft listing 99" in r and "uploaded digital file t.pdf" in r and "uploaded image a.png" in r, r
    tags = [x for x in LOG if x[0] == "POST" and x[1].endswith("/shops/42/listings")][0][4]
    assert tags.count("budget") == 1
    for bad in ({"title": "x" * 141, "description": "d", "price": 1, "taxonomy_id": 2}, {"title": "t", "description": "d", "price": 1}):
        try: await tools.t_etsy_draft(ctx2, bad); assert False
        except tools.ToolError as e: assert "140" in str(e) or "missing" in str(e)
    try: await tools.t_etsy_draft(ctx2, {"title": "t", "description": "d", "price": 1, "taxonomy_id": 2, "file": "../../etc/passwd"}); assert False
    except Exception: pass
    assert "updated state" in await tools.t_etsy_update(ctx2, {"listing_id": 99, "state": "active"})
    try: await tools.t_etsy_update(ctx2, {"listing_id": 99, "title": "boom"}); assert False
    except tools.ToolError as e: assert "title too long" in str(e)
    try: await tools.t_etsy_update(ctx2, {"listing_id": 99}); assert False
    except tools.ToolError: pass

    # token refresh when close to expiry
    d = kv.get("etsy"); d["token"]["expires_at"] = time.time() + 5; await kv.put("etsy", d)
    await tools.t_etsy_read(ctx, {"what": "shop"})
    assert kv.get("etsy")["token"]["access_token"] == "777.def" and kv.get("etsy")["token"]["refresh_token"] == "r2"

    # permissions
    s = {"mode": "yolo", "allow": []}
    assert perms.decide(s, "etsy_update", {})[0] == "ask" and perms.always_rule("etsy_update", {}) is None
    assert perms.decide({"mode": "plan"}, "etsy_draft", {})[0] == "deny" and perms.decide({"mode": "ask", "allow": []}, "etsy_draft", {})[0] == "ask"
    assert perms.decide({"mode": "auto", "allow": []}, "etsy_draft", {})[0] == "allow"
    assert "etsy_read" in tools.READONLY and "etsy_update" in perms.ALWAYS_ASK
    names = {t["function"]["name"] for t in tools.schemas({"mode": "ask"}, None)}
    assert {"etsy_read", "etsy_draft", "etsy_update"} <= names
    await etsy.disconnect(); assert not etsy.connected()
    print("etsy OK")
asyncio.run(main())
