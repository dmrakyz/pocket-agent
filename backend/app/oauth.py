"""OAuth 2.1 (authorization code + PKCE) for remote MCP servers: discovery, dynamic client registration,
token exchange and refresh. Tokens live in the private state repo (kv 'oauth')."""
import asyncio
import base64
import hashlib
import re
import secrets
import time
from urllib.parse import urlencode, urlparse

import httpx

from . import kv

_transport = None            # tests inject an httpx transport here
_pending: dict[str, dict] = {}   # state -> flow in progress (in memory, 10 minutes)
CLIENT_NAME = "Pocket Agent"


class OAuthError(Exception):
    pass


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=20, follow_redirects=False, transport=_transport,
                             headers={"User-Agent": "PocketAgent/1.0", "Accept": "application/json"})


async def _public(url: str):
    from . import tools
    if urlparse(url).scheme != "https":
        raise OAuthError(f"refusing non-https URL: {url}")
    try:
        await asyncio.to_thread(tools._check_public, url)
    except tools.ToolError as e:
        raise OAuthError(str(e))


async def _json(c: httpx.AsyncClient, url: str):
    await _public(url)
    try:
        r = await c.get(url)
        if r.status_code != 200:
            return None
        return r.json()
    except (httpx.HTTPError, ValueError):
        return None


def _db() -> dict:
    return kv.get("oauth", {}) or {}


async def _save(db: dict):
    await kv.put("oauth", db)


async def discover(mcp_url: str) -> dict:
    u = urlparse(mcp_url)
    origin = f"{u.scheme}://{u.netloc}"
    async with _client() as c:
        rm_url = None
        try:
            await _public(mcp_url)
            r = await c.post(mcp_url, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "probe", "version": "1"}}},
                headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"})
            m = re.search(r'resource_metadata="([^"]+)"', r.headers.get("www-authenticate", ""))
            if m:
                rm_url = m.group(1)
        except httpx.HTTPError:
            pass
        cands = ([rm_url] if rm_url else []) + [f"{origin}/.well-known/oauth-protected-resource{u.path.rstrip('/')}",
                                                  f"{origin}/.well-known/oauth-protected-resource"]
        as_url, scopes = origin, None
        for cand in cands:
            try:
                prm = await _json(c, cand)
            except OAuthError:
                continue
            if prm and prm.get("authorization_servers"):
                as_url = prm["authorization_servers"][0]
                scopes = prm.get("scopes_supported")
                break
        a = urlparse(as_url)
        ao, ap = f"{a.scheme}://{a.netloc}", a.path.rstrip("/")
        meta = None
        for cand in (f"{ao}/.well-known/oauth-authorization-server{ap}", f"{ao}/.well-known/openid-configuration{ap}",
                     f"{as_url.rstrip('/')}/.well-known/openid-configuration"):
            try:
                meta = await _json(c, cand)
            except OAuthError:
                meta = None
            if meta and meta.get("authorization_endpoint") and meta.get("token_endpoint"):
                break
            meta = None
    if not meta:   # MCP spec fallback paths
        meta = {"authorization_endpoint": f"{ao}/authorize", "token_endpoint": f"{ao}/token", "registration_endpoint": f"{ao}/register"}
    for k in ("authorization_endpoint", "token_endpoint"):
        await _public(meta[k])
    if meta.get("registration_endpoint"):
        await _public(meta["registration_endpoint"])
    return {"authorization_endpoint": meta["authorization_endpoint"], "token_endpoint": meta["token_endpoint"],
            "registration_endpoint": meta.get("registration_endpoint"), "scopes": scopes}


async def start(name: str, mcp_url: str, redirect_base: str) -> str:
    """Begin a sign-in. Returns the URL the person must open."""
    if urlparse(redirect_base).scheme != "https" and urlparse(redirect_base).hostname not in ("localhost", "127.0.0.1"):
        raise OAuthError("the app's address must be https")
    redirect_uri = redirect_base.rstrip("/") + "/oauth/callback"
    ep = await discover(mcp_url)
    db = _db()
    ent = db.get(name) or {}
    if ent.get("redirect_uri") != redirect_uri or ent.get("token_endpoint") != ep["token_endpoint"] or not ent.get("client_id"):
        if not ep["registration_endpoint"]:
            raise OAuthError("this server does not allow automatic app registration")
        async with _client() as c:
            r = await c.post(ep["registration_endpoint"], json={
                "client_name": CLIENT_NAME, "redirect_uris": [redirect_uri], "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"], "token_endpoint_auth_method": "none"})
        if r.status_code not in (200, 201):
            raise OAuthError(f"app registration failed (HTTP {r.status_code})")
        reg = r.json()
        ent = {"client_id": reg["client_id"], "client_secret": reg.get("client_secret"), "redirect_uri": redirect_uri}
    ent.update(authorization_endpoint=ep["authorization_endpoint"], token_endpoint=ep["token_endpoint"], url=mcp_url)
    db[name] = ent
    await _save(db)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    now = time.time()
    for k in [k for k, v in _pending.items() if now - v["t"] > 600]:
        _pending.pop(k, None)
    _pending[state] = {"name": name, "verifier": verifier, "t": now}
    q = {"response_type": "code", "client_id": ent["client_id"], "redirect_uri": redirect_uri, "state": state,
         "code_challenge": challenge, "code_challenge_method": "S256", "resource": mcp_url}
    if ep.get("scopes"):
        q["scope"] = " ".join(ep["scopes"])
    sep = "&" if "?" in ep["authorization_endpoint"] else "?"
    return ep["authorization_endpoint"] + sep + urlencode(q)


async def _token_call(ent: dict, data: dict) -> dict:
    data = {**data, "client_id": ent["client_id"], "resource": ent["url"]}
    if ent.get("client_secret"):
        data["client_secret"] = ent["client_secret"]
    async with _client() as c:
        await _public(ent["token_endpoint"])
        r = await c.post(ent["token_endpoint"], data=data, headers={"Accept": "application/json"})
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200 or not body.get("access_token"):
        raise OAuthError(body.get("error_description") or body.get("error") or f"token request failed (HTTP {r.status_code})")
    return body


def _store_token(ent: dict, body: dict):
    old = ent.get("token") or {}
    ent["token"] = {"access_token": body["access_token"],
                    "refresh_token": body.get("refresh_token") or old.get("refresh_token"),
                    "expires_at": time.time() + int(body.get("expires_in") or 3600)}


async def finish(state: str, code: str) -> str:
    """Handle the redirect back from the login page. Returns the connector name."""
    flow = _pending.pop(state, None)
    if not flow or time.time() - flow["t"] > 600:
        raise OAuthError("this sign-in link expired, start again from the app")
    db = _db()
    ent = db.get(flow["name"])
    if not ent:
        raise OAuthError("unknown connector")
    body = await _token_call(ent, {"grant_type": "authorization_code", "code": code,
                                   "redirect_uri": ent["redirect_uri"], "code_verifier": flow["verifier"]})
    _store_token(ent, body)
    db[flow["name"]] = ent
    await _save(db)
    return flow["name"]


def signed_in(name: str) -> bool:
    return bool((_db().get(name) or {}).get("token"))


async def header(name: str):
    """Authorization header for a connector, refreshing the token when it is about to expire."""
    db = _db()
    ent = db.get(name)
    tok = (ent or {}).get("token")
    if not tok:
        return None
    if tok["expires_at"] - time.time() < 60:
        if not tok.get("refresh_token"):
            return None
        try:
            body = await _token_call(ent, {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        except OAuthError:
            ent.pop("token", None)
            await _save(db)
            return None
        _store_token(ent, body)
        await _save(db)
        tok = ent["token"]
    return "Bearer " + tok["access_token"]


async def forget(name: str):
    db = _db()
    if db.pop(name, None) is not None:
        await _save(db)
