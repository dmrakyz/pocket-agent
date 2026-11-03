"""Small JSON documents (push subscriptions, routines, connectors) kept on disk and mirrored to the private state repo.
Render's disk is wiped on restart, so the state repo is the source of truth at boot."""
import asyncio
import base64
import copy
import json
from pathlib import Path

import httpx

from . import config, db, store

DIR = Path(config.DATA_DIR) / "kv"
_cache: dict = {}
_shas: dict[str, str] = {}
_locks: dict[str, asyncio.Lock] = {}


def get(name: str, default=None):
    return copy.deepcopy(_cache.get(name, default))


async def put(name: str, value) -> None:
    _cache[name] = copy.deepcopy(value)
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
    if db.enabled():
        try:
            await db.kv_put(name, value)          # fast path: survives redeploys, no git commit
        except Exception as e:  # noqa: BLE001
            store.remote_status["error"] = f"turso: {e}"[:200]
    async with _locks.setdefault(name, asyncio.Lock()):
        await _remote_put(name, value)


async def _remote_put(name: str, value):
    if not store.remote_status["enabled"]:
        return
    url = f"{store.API}/repos/{config.STATE_REPO}/contents/kv/{name}.json"
    body = {"message": f"kv {name}", "content": base64.b64encode(json.dumps(value, indent=1).encode()).decode()}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            for _ in range(3):
                if name in _shas:
                    body["sha"] = _shas[name]
                r = await c.put(url, headers=store._gh_headers(), json=body)
                if r.status_code in (200, 201):
                    _shas[name] = r.json()["content"]["sha"]
                    return
                if r.status_code in (409, 422):
                    g = await c.get(url, headers=store._gh_headers())
                    if g.status_code == 200:
                        _shas[name] = g.json()["sha"]
                    else:
                        _shas.pop(name, None); body.pop("sha", None)
                    continue
                store.remote_status["error"] = f"kv PUT {r.status_code}: {r.text[:160]}"
                return
    except Exception as e:  # noqa: BLE001
        store.remote_status["error"] = f"kv {type(e).__name__}: {e}"


async def init():
    """Load local copies, then the state repo's copies (which win: local disk may be a fresh container)."""
    DIR.mkdir(parents=True, exist_ok=True)
    for f in DIR.glob("*.json"):
        try:
            _cache[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    if db.enabled():
        try:
            loaded = await db.kv_load()
            for k, v in loaded.items():                   # Turso wins over the git mirror
                _cache[k] = v
                (DIR / f"{k}.json").write_text(json.dumps(v), encoding="utf-8")
            have = set(loaded)
        except Exception as e:  # noqa: BLE001
            have = set()
            store.remote_status["error"] = f"turso load: {e}"[:200]
    else:
        have = set()
    if not store.remote_status["enabled"]:
        return
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            lst = await c.get(f"{store.API}/repos/{config.STATE_REPO}/contents/kv", headers=store._gh_headers())
            if lst.status_code != 200:
                return
            for item in lst.json():
                if not item["name"].endswith(".json"):
                    continue
                g = await c.get(item["url"], headers={**store._gh_headers(), "Accept": "application/vnd.github.raw+json"})
                if g.status_code == 200:
                    name = item["name"][:-5]
                    _shas[name] = item["sha"]
                    if name in have and db.enabled():
                        continue              # Turso already supplied it
                    _cache[name] = g.json()
                    if db.enabled():
                        try:
                            await db.kv_put(name, _cache[name])      # first run with Turso: seed it from the git copy
                        except Exception:  # noqa: BLE001
                            pass
    except Exception as e:  # noqa: BLE001
        store.remote_status["error"] = f"kv load {type(e).__name__}: {e}"
