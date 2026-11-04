"""Lemon Squeezy (merchant of record) through its public JSON:API. Auth is the account's API key (LEMONSQUEEZY_API_KEY).

Lemon Squeezy's API cannot create or edit products, variants or files (read-only), so the user creates each product once in the
dashboard; the agent reads them back, makes checkout links, watches orders, and can refund (always with approval).
"""
import httpx

from . import config

BASE = config.env("LEMONSQUEEZY_API_BASE", "https://api.lemonsqueezy.com/v1")
_transport = None            # tests inject a fake


class LsError(Exception):
    pass


def configured() -> bool:
    return bool(config.LEMONSQUEEZY_API_KEY)


async def call(method: str, path: str, params: dict | None = None, body: dict | None = None):
    if not configured():
        raise LsError("LEMONSQUEEZY_API_KEY is not set on the server (Settings → Lemon Squeezy explains how)")
    h = {"Accept": "application/vnd.api+json", "Content-Type": "application/vnd.api+json", "Authorization": "Bearer " + config.LEMONSQUEEZY_API_KEY}
    async with httpx.AsyncClient(timeout=30, transport=_transport) as c:
        r = await c.request(method, BASE + path, params=params, json=body, headers=h)
    try:
        j = r.json()
    except ValueError:
        j = {}
    if r.status_code == 429:
        raise LsError("Lemon Squeezy rate limit hit (300/min); retry shortly")
    if r.status_code >= 400:
        errs = j.get("errors") or []
        msg = "; ".join(f"{e.get('title', '')}: {e.get('detail', '')}" for e in errs[:3]) or r.text[:200]
        raise LsError(f"Lemon Squeezy said {r.status_code}: {msg}")
    return j


def _rows(j) -> list[dict]:
    d = j.get("data")
    d = d if isinstance(d, list) else ([d] if d else [])
    return [{"id": x.get("id"), **(x.get("attributes") or {})} for x in d]


async def store_id() -> str:
    if config.LEMONSQUEEZY_STORE_ID:
        return config.LEMONSQUEEZY_STORE_ID
    rows = _rows(await call("GET", "/stores"))
    if not rows:
        raise LsError("this Lemon Squeezy account has no store yet; create one in the dashboard first")
    return str(rows[0]["id"])


async def status() -> dict:
    out = {"configured": configured(), "store": "", "mode": "", "error": ""}
    if not configured():
        return out
    try:
        rows = _rows(await call("GET", "/stores"))
        if rows:
            out["store"] = f"{rows[0].get('name')} ({rows[0].get('currency')})"
        me = _rows(await call("GET", "/users/me"))
        out["mode"] = "test" if me and me[0].get("test_mode") else ""
        if not rows:
            out["error"] = "key works, but there is no store yet"
    except LsError as e:
        out["error"] = str(e)
    return out


async def read(what: str, product_id: str = "", limit: int = 25) -> str:
    n = max(1, min(int(limit or 25), 50))
    sid = await store_id()
    p = {"page[size]": n}
    if what == "store":
        r = _rows(await call("GET", f"/stores/{sid}"))[0]
        return (f"{r.get('name')} · {r.get('currency')} · total sales {r.get('total_sales')} · revenue {r.get('total_revenue')} · "
                f"30-day sales {r.get('thirty_day_sales')} / revenue {r.get('thirty_day_revenue')} · {r.get('url')}")
    if what == "products":
        rows = _rows(await call("GET", "/products", {**p, "filter[store_id]": sid}))
        return "\n".join(f"- {x['id']} [{x.get('status')}] {x.get('name')} · {x.get('price_formatted')} · buy: {x.get('buy_now_url')}" for x in rows) or "No products yet. The user creates them in the Lemon Squeezy dashboard (the API is read-only for products)."
    if what == "variants":
        q = {**p, **({"filter[product_id]": product_id} if product_id else {})}
        rows = _rows(await call("GET", "/variants", q))
        return "\n".join(f"- variant {x['id']} (product {x.get('product_id')}) [{x.get('status')}] {x.get('name')} · {x.get('price')} cents" for x in rows) or "No variants."
    if what == "orders":
        rows = _rows(await call("GET", "/orders", {**p, "filter[store_id]": sid, "sort": "-createdAt"}))
        return "\n".join(f"- order {x['id']} #{x.get('order_number')} {x.get('status')}{' (refunded)' if x.get('refunded') else ''} · {x.get('total_formatted')} · {x.get('user_email')} · {str(x.get('created_at', ''))[:10]} · {(x.get('first_order_item') or {}).get('product_name', '')}" for x in rows) or "No orders yet."
    if what == "customers":
        rows = _rows(await call("GET", "/customers", {**p, "filter[store_id]": sid}))
        return "\n".join(f"- {x.get('name')} <{x.get('email')}> · {x.get('total_revenue_currency_formatted', '')} · {x.get('status')}" for x in rows) or "No customers yet."
    raise LsError("what must be one of: store, products, variants, orders, customers")


async def checkout(variant_id: str, email: str = "", discount_code: str = "") -> str:
    sid = await store_id()
    attrs: dict = {}
    data = {}
    if email:
        data["email"] = email
    if discount_code:
        data["discount_code"] = discount_code
    if data:
        attrs["checkout_data"] = data
    body = {"data": {"type": "checkouts", "attributes": attrs, "relationships": {
        "store": {"data": {"type": "stores", "id": str(sid)}}, "variant": {"data": {"type": "variants", "id": str(variant_id)}}}}}
    r = _rows(await call("POST", "/checkouts", body=body))
    if not r or not r[0].get("url"):
        raise LsError("Lemon Squeezy returned no checkout URL")
    return r[0]["url"]


async def refund(order_id: str) -> str:
    r = await call("POST", f"/orders/{order_id}/refund", body={"data": {"type": "orders", "id": str(order_id)}})
    rows = _rows(r)
    return f"Refund requested for order {order_id}" + (f" (status now: {rows[0].get('status')})" if rows else "") + "."
