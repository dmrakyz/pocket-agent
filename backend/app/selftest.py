"""Platform check: fast, no-model self-test of the server and every feature, so you can tell what broke after a deploy.

Each check is isolated, bounded in time and leaves no trace. Status: ok / warn (works but worth a look) / fail / skip (not set up, which is fine).
"""
import asyncio
import json
import time

from . import apps, config, forge, kv, llm, mcp_server, push, routines, store, world

TEST_ID = "__selftest__"


def _rss_mb() -> float:
    try:
        for line in open("/proc/self/status"):
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


async def _kv_roundtrip():
    v = {"t": time.time()}
    await kv.put(TEST_ID, v)
    ok = kv.get(TEST_ID) == v
    await kv.put(TEST_ID, {})
    where = "GitHub state repo" if store.remote_status["enabled"] else "local disk only (chats vanish when Render restarts: set GITHUB_TOKEN)"
    return ("ok" if ok and store.remote_status["enabled"] else "warn" if ok else "fail"), f"read/write works; saved to {where}"


async def _model(mcp_client):
    ref = await llm.default_model()
    if not ref:
        return "fail", "no model available: add NVIDIA_API_KEY or another provider key"
    t0 = time.time()

    async def nop(*_a):
        return None
    try:
        await asyncio.wait_for(llm.chat(ref, [{"role": "user", "content": "Reply with the single word: pong"}], [], nop, nop, nop, thinking=False, max_tokens=20, fallback=False), 60)
    except Exception as e:  # noqa: BLE001
        return "fail", f"{ref} did not answer: {str(e)[:120]}"
    ms = int((time.time() - t0) * 1000)
    return ("ok" if ms < 15000 else "warn"), f"{ref} answered in {ms} ms"


async def _apps():
    html = "<!doctype html><html><head><title>t</title></head><body><script>localStorage.setItem('a','b')</script></body></html>"
    a = await apps.save({"name": "selftest", "id": None, "html": html})
    try:
        page = apps.with_sdk(apps.html_of(a["id"]), a["id"])
        ok = "window.agent=" in page and "sandbox allow-scripts" in apps.CSP and "connect-src 'none'" in apps.CSP and "allow-same-origin" not in apps.CSP and "localStorage" in page
    finally:
        await apps.delete(a["id"])
    return ("ok" if ok else "fail"), "save, serve with SDK, strict CSP (no network, opaque origin), delete"


async def _forge():
    r = await forge.sandbox("def run(a):\n    return a['x'] * 2", {"x": 21}, False)
    if not (r["ok"] and str(r["result"]) == "42"):
        return "fail", f"sandbox could not run a tool: {r}"
    n = await forge.sandbox("def run(a):\n    import socket\n    socket.create_connection(('1.1.1.1', 53), 2)\n    return 'LEAK'", {}, False)
    if n["ok"]:
        return "fail", "a no-network tool reached the internet"
    return "ok", f"runs tools, blocks the network ({len(forge.all_tools())} forged tool(s) stored)"


async def _mcp_server():
    r = await mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    t = await mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    ok = r["result"]["serverInfo"]["name"] == "pocket-agent" and len(t["result"]["tools"]) == 3
    return ("ok" if ok else "fail"), ("handshake and tool list fine; " + ("a token exists" if mcp_server.enabled() else "no token made yet (Settings → Agent as MCP server)"))


async def _worlds():
    beat = world.parse_beat('```json\n{"events":[{"who":"A","kind":"say","text":"hi"}],"memory":{"A":"x"}}\n```')
    cast = world.parse_cast("Mara: captain | route\nOtto: clerk | leave")
    ws = world.all_worlds()
    running = [w for w in ws if w.get("enabled")]
    bad = [w["name"] for w in ws if w.get("error")]
    if not (beat["events"] and len(cast) == 2):
        return "fail", "beat or cast parsing is broken"
    if bad:
        return "warn", f"parsing fine, but {', '.join(bad)} reported an error (open Worlds)"
    return "ok", f"parsing fine; {len(ws)} world(s), {len(running)} running"


async def _routines():
    r = routines.clean({"name": "t", "prompt": "p", "schedule": {"kind": "daily", "time": "08:00"}, "tz": "Europe/Istanbul"})
    nxt = routines.next_run(r, time.time())
    if not nxt or nxt <= time.time():
        return "fail", "could not compute the next run"
    rs = routines.all_routines()
    return "ok", f"scheduler computes next runs; {len(rs)} routine(s), {sum(1 for x in rs if x.get('enabled'))} on"


async def _connectors(mcp_client):
    srv = mcp_client.servers()
    if not srv:
        return "skip", "none connected (Settings → Connectors)"
    bad = [s["name"] for s in srv if str(mcp_client.report.get(s["name"], "")).startswith(("error", "needs sign-in"))]
    detail = "; ".join(f"{s['name']}: {mcp_client.report.get(s['name'], 'not connected yet')}" for s in srv)
    return ("warn" if bad else "ok"), detail


def _push():
    if not push.AVAILABLE:
        return "skip", "pywebpush not installed"
    n = len(push.subs())
    return ("ok" if n else "warn"), (f"{n} device(s) subscribed" if n else "no device subscribed: Settings → Push notifications")


def _memory():
    mb = _rss_mb()
    if not mb:
        return "skip", "cannot read memory use"
    return ("ok" if mb < 330 else "warn" if mb < 430 else "fail"), f"server is using {mb:.0f} MB of the 512 MB free tier"


def _browser():
    try:
        import playwright  # noqa: F401
        from pathlib import Path
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return "fail", f"Playwright is missing: {type(e).__name__}"
    return "ok", "Playwright is installed (the browser itself starts on first use, so it is not launched here)"


def _toolchains():
    import shutil
    from . import packs
    have = [t for t in ("python3", "g++", "gcc", "make", "node", "git") if shutil.which(t)]
    miss = [t for t in ("python3", "g++", "make", "node", "git") if t not in have]
    on = [p["name"] for p in packs.status() if p["installed"]]
    detail = "base: " + ", ".join(have) + (f"; MISSING: {', '.join(miss)}" if miss else "") + f"; packs on: {', '.join(on) or 'none'}; disk free {packs.free_mb()} MB; bash capped at {config.BASH_MEM_MB} MB per command"
    return ("fail" if miss else "ok"), detail


async def _turso():
    from . import db
    if not db.enabled():
        return "skip", "not set (TURSO_URL + TURSO_TOKEN): state lives in the GitHub state repo only and in-flight data is lost on redeploy"
    try:
        n = (await db.run([("SELECT COUNT(*) AS n FROM kv", [])]))[0][0]["n"]
    except Exception as e:  # noqa: BLE001
        return "fail", f"Turso is configured but not answering: {str(e)[:160]}"
    return "ok", f"connected; {n} state documents stored"


def _optional():
    out = []
    for name, on, hint in (("send_email", bool(config.RESEND_API_KEY), "RESEND_API_KEY"), ("Telegram", bool(config.TELEGRAM_BOT_TOKEN), "TELEGRAM_BOT_TOKEN"),
                           ("Instagram DMs", bool(config.INSTAGRAM_ACCESS_TOKEN), "INSTAGRAM_ACCESS_TOKEN"), ("vision model", bool(config.VISION_MODEL), "VISION_MODEL"),
                           ("egress proxy", bool(getattr(config, "EGRESS_PROXY", "")), "EGRESS_PROXY")):
        out.append((name, "ok" if on else "skip", "configured" if on else f"not set ({hint})"))
    return out


async def run(mcp_client) -> dict:
    results = []

    async def one(name, coro):
        t0 = time.time()
        try:
            st, detail = await asyncio.wait_for(coro, 90) if asyncio.iscoroutine(coro) else coro
        except Exception as e:  # noqa: BLE001
            st, detail = "fail", f"{type(e).__name__}: {str(e)[:160]}"
        results.append({"name": name, "status": st, "detail": detail, "ms": int((time.time() - t0) * 1000)})

    await one("State saving", _kv_roundtrip())
    await one("Default model", _model(mcp_client))
    await one("Memory use", _memory())
    await one("App drawer", _apps())
    await one("Tool forging", _forge())
    await one("Agent as MCP server", _mcp_server())
    await one("World engine", _worlds())
    await one("Routines", _routines())
    await one("Connectors", _connectors(mcp_client))
    await one("Push notifications", _push())
    await one("Browser", _browser())
    await one("Compilers & packs", _toolchains())
    await one("Database (Turso)", _turso())
    for name, st, detail in _optional():
        results.append({"name": name, "status": st, "detail": detail, "ms": 0})
    count = {k: sum(1 for r in results if r["status"] == k) for k in ("ok", "warn", "fail", "skip")}
    return {"results": results, "summary": count, "at": time.time()}
