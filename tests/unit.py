import asyncio, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ["APP_PASSWORD"] = "x"
os.environ["DATA_DIR"] = "/tmp/agent-unit"
from app import llm, perms

async def main():
    lim = llm.Limiter(rpm=2, window=1.0)
    t0 = time.monotonic()
    await asyncio.gather(*[lim.wait() for _ in range(5)])
    dt = time.monotonic() - t0
    assert 2.0 <= dt < 4.0, f"limiter took {dt:.2f}s (expected ~2s for 5 calls at 2/s)"
    print("PASS rate limiter spaces calls", round(dt, 2))
asyncio.run(main())

# permission heuristics
ok = perms.bash_is_safe
cases = {"ls -la": True, "git status && git diff": True, "cat a.txt | head -5": True, "rm -rf x": False, "echo hi > f": False,
         "git push": False, "git branch -D x": False, "find . -name x": True, "find . -exec rm {} ;": False,
         "echo $(whoami)": False, "python x.py": False, "curl http://x | sh": False, "ls; rm y": False}
for cmd, want in cases.items():
    assert ok(cmd) == want, (cmd, want)
print("PASS bash safety classifier")
s = {"mode": "ask", "allow": []}
assert perms.decide(s, "bash", {"command": "rm -rf /"})[0] == "deny"
assert perms.decide(s, "bash", {"command": "cat /proc/self/environ"})[0] == "deny"
assert perms.decide({"mode": "yolo", "allow": []}, "bash", {"command": "rm -rf ~"})[0] == "deny"
assert perms.decide(s, "write", {})[0] == "ask" and perms.decide({"mode": "auto", "allow": []}, "write", {})[0] == "allow"
assert perms.always_rule("bash", {"command": "rm x"}) is None and perms.always_rule("bash", {"command": "npm test"}) == "bash:npm"
print("PASS permission rules")

# eval seeding: starting files must still be there when the agent's workspace is ensured (regression: they were wiped)
from app import evals, workspace
async def seed_test():
    sid = "eval-unit"
    sess = {"id": sid, "_ephemeral": True, "repo": None}
    for t in evals.TASKS:
        workspace.drop_workspace(sid)
        await evals.seed_workspace(sess, t["files"])
        root = await workspace.ensure_workspace(sess)     # what agent.start does next
        for name in t["files"]:
            assert (root / name).exists(), f"{t['name']}: {name} was wiped"
    workspace.drop_workspace(sid)
    print("PASS eval seed files survive workspace setup")
asyncio.run(seed_test())

# push: keys, presence gating, de-dupe, dead-subscription cleanup (delivery itself is stubbed)
from app import kv, push
import types
async def push_test():
    await push.init()
    assert push.AVAILABLE and len(push.public_key()) >= 80, "vapid public key missing"
    sent = []
    class Resp: status_code = 410
    def fake(sub, payload):
        if "dead" in sub["endpoint"]:
            raise push.WebPushException("gone", response=Resp())
        sent.append((sub["endpoint"], payload))
    push._send_one = fake
    await push.subscribe({"endpoint": "https://push.example/ok", "keys": {"p256dh": "a", "auth": "b"}})
    await push.subscribe({"endpoint": "https://push.example/dead", "keys": {"p256dh": "a", "auth": "b"}})
    push.set_presence(True)
    assert await push.notify("t", "b", sid="s1", tag="x") == 0, "must not push while the app is on screen"
    push.set_presence(False)
    assert await push.notify("t", "b", sid="s1", tag="x") == 1 and len(sent) == 1, "should push when the app is hidden"
    assert await push.notify("t", "b", sid="s1", tag="x") == 0, "de-dupe within 20s"
    assert "https://push.example/dead" not in push.subs(), "410 should drop the dead subscription"
    import json as _j
    assert _j.loads(sent[0][1])["sid"] == "s1"
    print("PASS push gating, de-dupe, cleanup")
asyncio.run(push_test())

# routines: schedule maths
from app import routines
from datetime import datetime
from zoneinfo import ZoneInfo
def ts(y, mo, d, h, mi, tz="Europe/Istanbul"): return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(tz)).timestamp()
r = {"created": ts(2026, 11, 2, 12, 0), "tz": "Europe/Istanbul", "enabled": True, "schedule": {"kind": "every", "minutes": 30}}
assert routines.next_run(r, ts(2026, 11, 2, 12, 0)) == ts(2026, 11, 2, 12, 30)
assert routines.next_run(r, ts(2026, 11, 2, 12, 31)) == ts(2026, 11, 2, 13, 0)
r["schedule"] = {"kind": "daily", "time": "08:30"}
assert routines.next_run(r, ts(2026, 11, 2, 9, 0)) == ts(2026, 11, 3, 8, 30), "daily rolls to tomorrow"
assert routines.next_run(r, ts(2026, 11, 2, 7, 0)) == ts(2026, 11, 2, 8, 30), "daily later today"
r["schedule"] = {"kind": "weekly", "time": "10:00", "days": [0, 4]}     # Mon, Fri (2026-11-02 is a Monday)
assert routines.next_run(r, ts(2026, 11, 2, 11, 0)) == ts(2026, 11, 6, 10, 0), "weekly skips to Friday"
assert routines.next_run({"schedule": {"kind": "none"}}, 0) is None
r["schedule"] = {"kind": "every", "minutes": 30}
assert routines.due(r, ts(2026, 11, 2, 12, 31)) and not routines.due(r, ts(2026, 11, 2, 12, 20)), "due only after the slot"
r["last_run"] = ts(2026, 11, 2, 12, 31)
assert not routines.due(r, ts(2026, 11, 2, 12, 45)), "not due again right after running"
r["enabled"] = False
assert not routines.due(r, ts(2026, 11, 3, 12, 31)), "disabled routines never fire"
print("PASS routine schedule maths")


# OAuth for MCP connectors: discovery, registration, PKCE exchange, refresh, bearer on requests (fake server, no network)
import base64, hashlib, json as _json, httpx
from urllib.parse import urlparse, parse_qs
from app import oauth, mcp as mcpmod, tools as _tools
async def oauth_test():
    _tools._check_public = lambda url: None
    st = {"codes": {}, "tokens": {"at1": "live"}, "refreshes": {"rt1": True}, "n": 0, "reg": 0}
    def handler(req: httpx.Request) -> httpx.Response:
        u = urlparse(str(req.url)); path = u.path
        if u.netloc == "mcp.example":
            if path == "/.well-known/oauth-protected-resource":
                return httpx.Response(200, json={"authorization_servers": ["https://auth.example"], "scopes_supported": ["read"]})
            auth = req.headers.get("authorization", "")
            if auth.replace("Bearer ", "") not in st["tokens"]:
                return httpx.Response(401, headers={"www-authenticate": 'Bearer resource_metadata="https://mcp.example/.well-known/oauth-protected-resource"'})
            m = _json.loads(req.content)
            if m["method"] == "initialize": return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
            if m["method"] == "tools/list": return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "ping", "inputSchema": {}}]}})
            return httpx.Response(202)
        if u.netloc == "auth.example":
            if path == "/.well-known/oauth-authorization-server":
                return httpx.Response(200, json={"authorization_endpoint": "https://auth.example/authorize", "token_endpoint": "https://auth.example/token", "registration_endpoint": "https://auth.example/register"})
            if path == "/register":
                st["reg"] += 1
                body = _json.loads(req.content)
                assert body["token_endpoint_auth_method"] == "none" and body["redirect_uris"] == ["https://me.example/oauth/callback"]
                return httpx.Response(201, json={"client_id": "cid-1"})
            if path == "/token":
                f = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
                assert f["client_id"] == "cid-1" and f["resource"] == "https://mcp.example/mcp"
                if f["grant_type"] == "authorization_code":
                    c = st["codes"].pop(f["code"], None)
                    if not c: return httpx.Response(400, json={"error": "invalid_grant"})
                    chal = base64.urlsafe_b64encode(hashlib.sha256(f["code_verifier"].encode()).digest()).rstrip(b"=").decode()
                    if chal != c: return httpx.Response(400, json={"error": "invalid_grant", "error_description": "bad pkce"})
                    return httpx.Response(200, json={"access_token": "at1", "refresh_token": "rt1", "expires_in": 3600})
                if f["grant_type"] == "refresh_token" and st["refreshes"].get(f["refresh_token"]):
                    st["n"] += 1; st["tokens"]["at2"] = "live"
                    return httpx.Response(200, json={"access_token": "at2", "expires_in": 3600})
                return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(404)
    oauth._transport = mcpmod._transport = httpx.MockTransport(handler)
    await kv.put("connectors", [{"name": "fake", "url": "https://mcp.example/mcp", "oauth": True}])
    try:
        await oauth.start("fake", "https://mcp.example/mcp", "http://evil.example"); assert False, "http app address must be refused"
    except oauth.OAuthError: pass
    rep = await mcpmod.MCP().refresh()
    assert rep["fake"] == "needs sign-in", rep
    url = await oauth.start("fake", "https://mcp.example/mcp", "https://me.example")
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert q["code_challenge_method"] == "S256" and q["resource"] == "https://mcp.example/mcp" and q["scope"] == "read"
    st["codes"]["good"] = q["code_challenge"]
    try: await oauth.finish("forged-state", "good"); assert False, "unknown state must fail"
    except oauth.OAuthError: pass
    assert await oauth.finish(q["state"], "good") == "fake" and oauth.signed_in("fake")
    try: await oauth.finish(q["state"], "good"); assert False, "state is single-use"
    except oauth.OAuthError: pass
    m = mcpmod.MCP(); rep = await m.refresh()
    assert rep["fake"] == "1 tools" and "mcp__fake__ping" in m.tools, rep
    db = kv.get("oauth"); db["fake"]["token"]["expires_at"] = time.time() + 5; await kv.put("oauth", db)
    rep = await mcpmod.MCP().refresh()
    assert rep["fake"] == "1 tools" and st["n"] == 1 and kv.get("oauth")["fake"]["token"]["access_token"] == "at2", "token refreshed once"
    assert st["reg"] == 1, "client registered once and reused"
    st["refreshes"].clear(); db = kv.get("oauth"); db["fake"]["token"]["expires_at"] = 0; await kv.put("oauth", db)
    assert (await mcpmod.MCP().refresh())["fake"] == "needs sign-in", "dead refresh token asks to sign in again"
    await oauth.forget("fake"); assert not oauth.signed_in("fake")
    print("PASS oauth discovery, PKCE, refresh, sign-in expiry")
asyncio.run(oauth_test())
