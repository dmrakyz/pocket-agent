"""Etsy shop integration through the official Open API v3 (OAuth2 + PKCE). No password is ever stored.

Setup: create an app at etsy.com/developers, register the redirect URI shown in Settings → Etsy, set ETSY_API_KEY (and
ETSY_SHARED_SECRET if Etsy gave you one) on the server, then tap Connect. Tokens live in kv `etsy` (state repo / Turso).
Written from the public v3 API shape; the first real run is the first real test (see tests/etsy_test.py for the fake server).
"""
import base64
import hashlib
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from . import config, kv

BASE = config.env("ETSY_API_BASE", "https://api.etsy.com/v3/application")
AUTH_URL = "https://www.etsy.com/oauth/connect"
TOKEN_URL = config.env("ETSY_TOKEN_URL", "https://api.etsy.com/v3/public/oauth/token")
SCOPES = "listings_r listings_w shops_r shops_w transactions_r"
_transport = None                 # tests inject a fake Etsy
_pending: dict[str, dict] = {}    # state -> {verifier, redirect_uri, t}
MAX_UPLOAD = 20 * 1024 * 1024


class EtsyError(Exception):
    pass


def configured() -> bool:
    return bool(config.ETSY_API_KEY)


def _key_header() -> str:
    return f"{config.ETSY_API_KEY}:{config.ETSY_SHARED_SECRET}" if config.ETSY_SHARED_SECRET else config.ETSY_API_KEY


def _db() -> dict:
    return kv.get("etsy", {}) or {}


def connected() -> bool:
    return bool(_db().get("token") and _db().get("shop_id"))


def status() -> dict:
    d = _db()
    return {"configured": configured(), "connected": connected(), "shop": d.get("shop_name", ""), "shop_id": d.get("shop_id"),
            "error": d.get("error", "")}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=40, transport=_transport, headers={"User-Agent": "PocketAgent/1.0"})


def start(redirect_base: str) -> str:
    if not configured():
        raise EtsyError("ETSY_API_KEY is not set on the server yet")
    if not redirect_base.startswith("https://") and "localhost" not in redirect_base and "127.0.0.1" not in redirect_base:
        raise EtsyError("the app's address must be https")
    redirect_uri = redirect_base.rstrip("/") + "/etsy/callback"
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    now = time.time()
    for k in [k for k, v in _pending.items() if now - v["t"] > 600]:
        _pending.pop(k, None)
    _pending[state] = {"verifier": verifier, "redirect_uri": redirect_uri, "t": now}
    q = {"response_type": "code", "client_id": config.ETSY_API_KEY, "redirect_uri": redirect_uri, "scope": SCOPES, "state": state,
         "code_challenge": challenge, "code_challenge_method": "S256"}
    return AUTH_URL + "?" + urlencode(q)


async def _token_call(data: dict) -> dict:
    async with _client() as c:
        r = await c.post(TOKEN_URL, data={**data, "client_id": config.ETSY_API_KEY})
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200 or not body.get("access_token"):
        raise EtsyError(body.get("error_description") or body.get("error") or f"Etsy token request failed (HTTP {r.status_code})")
    return body


def _tok(body: dict, old: dict | None = None) -> dict:
    return {"access_token": body["access_token"], "refresh_token": body.get("refresh_token") or (old or {}).get("refresh_token"),
            "expires_at": time.time() + int(body.get("expires_in") or 3600)}


async def finish(state: str, code: str) -> str:
    flow = _pending.pop(state, None)
    if not flow or time.time() - flow["t"] > 600:
        raise EtsyError("this sign-in link expired, start again from the app")
    body = await _token_call({"grant_type": "authorization_code", "code": code, "redirect_uri": flow["redirect_uri"], "code_verifier": flow["verifier"]})
    d = {"token": _tok(body), "user_id": str(body["access_token"]).split(".")[0]}
    await kv.put("etsy", d)
    shop = await _find_shop(d["user_id"])
    d.update(shop_id=shop["shop_id"], shop_name=shop.get("shop_name", ""), error="")
    await kv.put("etsy", d)
    return d["shop_name"] or str(d["shop_id"])


async def _find_shop(user_id: str) -> dict:
    r = await call("GET", f"/users/{user_id}/shops")
    shop = r if isinstance(r, dict) and r.get("shop_id") else ((r.get("results") or [None])[0] if isinstance(r, dict) else None)
    if not shop or not shop.get("shop_id"):
        raise EtsyError("signed in, but this Etsy account has no shop yet; open the shop in Etsy first")
    return shop


async def disconnect():
    await kv.put("etsy", {})


async def _bearer() -> str:
    d = _db()
    tok = d.get("token")
    if not tok:
        raise EtsyError("Etsy is not connected. The user connects it in Settings → Etsy.")
    if tok["expires_at"] - time.time() < 120:
        if not tok.get("refresh_token"):
            raise EtsyError("Etsy sign-in expired; the user must reconnect in Settings → Etsy")
        try:
            body = await _token_call({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        except EtsyError as e:
            d["error"] = f"refresh failed: {e}"
            await kv.put("etsy", d)
            raise EtsyError("Etsy sign-in expired; the user must reconnect in Settings → Etsy")
        d["token"] = _tok(body, tok)
        await kv.put("etsy", d)
        tok = d["token"]
    return tok["access_token"]


async def call(method: str, path: str, params: dict | None = None, data: dict | None = None, files: dict | None = None):
    """One API call. Raises EtsyError with Etsy's own message so the agent can fix its request."""
    if not configured():
        raise EtsyError("ETSY_API_KEY is not set on the server")
    headers = {"x-api-key": _key_header(), "Authorization": "Bearer " + await _bearer()}
    async with _client() as c:
        r = await c.request(method, BASE + path, params=params, data=data, files=files, headers=headers)
        if r.status_code in (404, 405) and method == "PATCH":     # some endpoints are still PUT
            r = await c.request("PUT", BASE + path, params=params, data=data, files=files, headers=headers)
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 429:
        raise EtsyError("Etsy rate limit hit; wait a bit and retry")
    if r.status_code >= 400:
        msg = body.get("error") or body.get("message") or r.text[:200]
        raise EtsyError(f"Etsy said {r.status_code}: {msg}")
    return body


def shop_id() -> int:
    sid = _db().get("shop_id")
    if not sid:
        raise EtsyError("Etsy is not connected. The user connects it in Settings → Etsy.")
    return sid


# ----------------------------------------------------------------------------- what the tools use
def _money(m) -> str:
    if isinstance(m, dict) and "amount" in m:
        return f"{int(m['amount']) / int(m.get('divisor') or 100):.2f} {m.get('currency_code', '')}".strip()
    return str(m)


async def read(what: str, state: str = "", listing_id: int = 0, limit: int = 25, query: str = "") -> str:
    sid = shop_id()
    limit = max(1, min(int(limit or 25), 50))
    if what == "shop":
        s = await call("GET", f"/shops/{sid}")
        return (f"{s.get('shop_name')} · {s.get('listing_active_count', '?')} active listings · {s.get('transaction_sold_count', '?')} sales · "
                f"{s.get('review_count', '?')} reviews (avg {s.get('review_average', '?')}) · currency {s.get('currency_code', '?')}")
    if what == "listings":
        r = await call("GET", f"/shops/{sid}/listings", {"state": state or "active", "limit": limit})
        rows = r.get("results", [])
        return "\n".join(f"- {x['listing_id']} [{x.get('state')}] {x.get('title', '')[:90]} · {_money(x.get('price'))} · {x.get('views', 0)} views · {x.get('num_favorers', 0)} favs"
                         for x in rows) or f"No {state or 'active'} listings."
    if what == "listing":
        if not listing_id:
            raise EtsyError("listing_id is required")
        x = await call("GET", f"/listings/{listing_id}")
        return (f"{x['listing_id']} [{x.get('state')}] {x.get('title')}\nprice {_money(x.get('price'))} · tags: {', '.join(x.get('tags', []))}\n"
                f"views {x.get('views', 0)} · favorites {x.get('num_favorers', 0)}\n\n{(x.get('description') or '')[:3000]}")
    if what == "orders":
        r = await call("GET", f"/shops/{sid}/receipts", {"limit": limit})
        rows = r.get("results", [])
        out = []
        for x in rows:
            items = ", ".join(t.get("title", "")[:40] for t in x.get("transactions", [])[:3])
            out.append(f"- #{x.get('receipt_id')} {'paid' if x.get('is_paid') else 'unpaid'}{' shipped' if x.get('is_shipped') else ''} · {_money(x.get('grandtotal'))} · {items}")
        return "\n".join(out) or "No orders yet."
    if what == "reviews":
        r = await call("GET", f"/shops/{sid}/reviews", {"limit": limit})
        return "\n".join(f"- {x.get('rating')}★ listing {x.get('listing_id')}: {(x.get('review') or '(no text)')[:200]}" for x in r.get("results", [])) or "No reviews yet."
    if what == "taxonomy":
        r = await call("GET", "/seller-taxonomy/nodes")
        q = query.lower().split()
        out = []

        def walk(nodes, path):
            for n in nodes:
                p = path + [n.get("name", "")]
                kids = n.get("children") or []
                label = " > ".join(p)
                if q and all(w in label.lower() for w in q):
                    out.append(f"{n['id']}: {label}")
                walk(kids, p)
        walk(r.get("results", []), [])
        return "\n".join(out[:40]) or "No taxonomy match; try fewer or different words."
    raise EtsyError("what must be one of: shop, listings, listing, orders, reviews, taxonomy")


DRAFT_REQUIRED = ("title", "description", "price", "taxonomy_id")


def _clean_tags(tags) -> list[str]:
    if isinstance(tags, str):
        tags = tags.split(",")
    out = []
    for t in tags or []:
        t = " ".join(str(t).split())[:20].strip()
        if t and t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return out[:13]


def _readable(path: Path) -> Path:
    if not path.is_file():
        raise EtsyError(f"{path.name} is not a file")
    if path.stat().st_size > MAX_UPLOAD:
        raise EtsyError(f"{path.name} is over 20 MB")
    return path


async def create_draft(a: dict, resolve) -> str:
    miss = [k for k in DRAFT_REQUIRED if not a.get(k)]
    if miss:
        raise EtsyError("missing: " + ", ".join(miss) + (" (look up taxonomy_id with etsy_read what=taxonomy)" if "taxonomy_id" in miss else ""))
    title = " ".join(str(a["title"]).split())
    if len(title) > 140:
        raise EtsyError(f"title is {len(title)} characters; Etsy allows 140")
    tags = _clean_tags(a.get("tags"))
    sid = shop_id()
    form = {"quantity": int(a.get("quantity") or 999), "title": title, "description": str(a["description"]), "price": float(a["price"]),
            "who_made": a.get("who_made") or "i_did", "when_made": a.get("when_made") or "2020_2026", "taxonomy_id": int(a["taxonomy_id"]),
            "type": "download", "is_supply": "false"}
    if tags:
        form["tags"] = ",".join(tags)
    lst = await call("POST", f"/shops/{sid}/listings", data=form)
    lid = lst.get("listing_id")
    notes = [f"Draft listing {lid} created (not visible to buyers)."]
    for f in ([a["file"]] if a.get("file") else []):
        p = _readable(resolve(f))
        with open(p, "rb") as fh:
            await call("POST", f"/shops/{sid}/listings/{lid}/files", data={"name": p.name}, files={"file": (p.name, fh.read())})
        notes.append(f"uploaded digital file {p.name}")
    for rank, f in enumerate((a.get("images") or [])[:10], 1):
        p = _readable(resolve(f))
        with open(p, "rb") as fh:
            await call("POST", f"/shops/{sid}/listings/{lid}/images", data={"rank": rank}, files={"image": (p.name, fh.read())})
        notes.append(f"uploaded image {p.name}")
    if len(tags) < 13:
        notes.append(f"only {len(tags)} of 13 tags used; more tags = more search surface")
    return " ".join(notes)


async def update(a: dict, resolve) -> str:
    lid = int(a.get("listing_id") or 0)
    if not lid:
        raise EtsyError("listing_id is required")
    sid = shop_id()
    form = {}
    for k in ("title", "description"):
        if a.get(k):
            form[k] = " ".join(str(a[k]).split()) if k == "title" else str(a[k])
    if form.get("title") and len(form["title"]) > 140:
        raise EtsyError("title is over 140 characters")
    if a.get("price") is not None:
        form["price"] = float(a["price"])
    if a.get("tags") is not None:
        form["tags"] = ",".join(_clean_tags(a["tags"]))
    if a.get("state"):
        if a["state"] not in ("active", "inactive", "draft"):
            raise EtsyError("state must be active, inactive or draft")
        form["state"] = a["state"]
    notes = []
    if form:
        await call("PATCH", f"/shops/{sid}/listings/{lid}", data=form)
        notes.append("updated " + ", ".join(form))
    if a.get("file"):
        p = _readable(resolve(a["file"]))
        with open(p, "rb") as fh:
            await call("POST", f"/shops/{sid}/listings/{lid}/files", data={"name": p.name}, files={"file": (p.name, fh.read())})
        notes.append(f"uploaded digital file {p.name}")
    for rank, f in enumerate((a.get("images") or [])[:10], 1):
        p = _readable(resolve(f))
        with open(p, "rb") as fh:
            await call("POST", f"/shops/{sid}/listings/{lid}/images", data={"rank": rank}, files={"image": (p.name, fh.read())})
        notes.append(f"uploaded image {p.name}")
    if not notes:
        raise EtsyError("nothing to change: pass title, description, price, tags, state, file or images")
    return f"Listing {lid}: " + "; ".join(notes) + "."
