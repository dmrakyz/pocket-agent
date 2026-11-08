"""Turso (libSQL over HTTP) client. Optional: with TURSO_URL + TURSO_TOKEN set, small JSON state is kept in Turso too,
so it survives redeploys and writes don't need a git commit. Without them everything works as before."""
import json
import time

import httpx

from . import config

URL = (config.env("TURSO_URL").replace("libsql://", "https://").replace("wss://", "https://").rstrip("/"))
TOKEN = config.env("TURSO_TOKEN")
status = {"enabled": bool(URL and TOKEN), "error": "", "ok_at": 0.0}
_ready = False


def enabled() -> bool:
    return status["enabled"]


def _arg(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    return {"type": "text", "value": str(v)}


def _val(c):
    t = c.get("type")
    if t == "null":
        return None
    if t == "integer":
        return int(c["value"])
    if t == "float":
        return float(c["value"])
    return c.get("value")


async def run(statements: list) -> list:
    """statements: [(sql, [args])]. Returns one list of row-dicts per statement. Raises RuntimeError on failure."""
    if not enabled():
        raise RuntimeError("turso is not configured")
    reqs = [{"type": "execute", "stmt": {"sql": sql, "args": [_arg(a) for a in (args or [])]}} for sql, args in statements]
    reqs.append({"type": "close"})
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(URL + "/v2/pipeline", headers={"Authorization": "Bearer " + TOKEN}, json={"requests": reqs})
    except httpx.HTTPError as e:
        status["error"] = f"{type(e).__name__}: {e}"[:200]
        raise RuntimeError(status["error"])
    if r.status_code != 200:
        status["error"] = f"HTTP {r.status_code}: {r.text[:160]}".replace(TOKEN, "***")
        raise RuntimeError(status["error"])
    out = []
    for item in r.json().get("results", [])[:len(statements)]:
        if item.get("type") != "ok":
            status["error"] = str((item.get("error") or {}).get("message", "query failed"))[:200]
            raise RuntimeError(status["error"])
        res = item["response"]["result"]
        names = [c["name"] for c in res.get("cols", [])]
        out.append([dict(zip(names, (_val(c) for c in row))) for row in res.get("rows", [])])
    status["error"], status["ok_at"] = "", time.time()
    return out


async def ensure():
    global _ready
    if _ready or not enabled():
        return
    await run([("CREATE TABLE IF NOT EXISTS kv (name TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL)", []), ("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL)", [])])
    _ready = True


async def kv_load() -> dict:
    await ensure()
    rows = (await run([("SELECT name, value, updated FROM kv", [])]))[0]
    out = {}
    for r in rows:
        try:
            out[r["name"]] = json.loads(r["value"])
        except (TypeError, ValueError):
            pass
    return out


async def kv_put(name: str, value):
    await ensure()
    await run([("INSERT INTO kv (name, value, updated) VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value, updated = excluded.updated",
                [name, json.dumps(value), time.time()])])


async def keepalive_loop(every: float = 86400.0, first: float = 30.0):
    """Turso archives a free database after ~10 days with no activity. One tiny write a day means that never happens."""
    import asyncio
    await asyncio.sleep(first)
    while True:
        try:
            await kv_put("_keepalive", {"at": time.time()})
        except Exception as e:  # noqa: BLE001
            print("turso keepalive failed:", e)
        await asyncio.sleep(every)


async def sessions_load() -> dict:
    await ensure()
    rows = (await run([("SELECT id, value, updated FROM sessions", [])]))[0]
    out = {}
    for row in rows:
        try:
            out[row["id"]] = json.loads(row["value"])
        except (TypeError, ValueError):
            pass
    return out


async def session_put(session_id: str, value: dict):
    await ensure()
    await run([("INSERT INTO sessions (id, value, updated) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET value = excluded.value, updated = excluded.updated", [session_id, json.dumps(value), time.time()])])


async def session_delete(session_id: str):
    await ensure()
    await run([("DELETE FROM sessions WHERE id = ?", [session_id])])
