"""Chat sessions: kept in memory + local JSON, mirrored to a private GitHub repo so restarts lose nothing."""
import asyncio
import base64
import json
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx

from . import config

SESS_DIR = Path(config.DATA_DIR) / "sessions"
API = "https://api.github.com"

sessions: dict[str, dict] = {}
_dirty: dict[str, float] = {}
_shas: dict[str, str] = {}
_flush_task: Optional[asyncio.Task] = None
remote_status = {"enabled": False, "error": ""}


def _gh_headers():
    return {"Authorization": f"Bearer {config.GITHUB_TOKEN}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def new_session(title: str = "New chat", repo: Optional[str] = None, model: str = "", mode: str = "ask") -> dict:
    sid = uuid.uuid4().hex[:16]
    s = {"id": sid, "title": title, "created": time.time(), "updated": time.time(), "model": model,
         "mode": mode, "thinking": True, "effort": "high", "review": False, "model_mode": "manual", "model_roles": {}, "repo": repo or None, "messages": [], "todos": [],
         "allow": [], "usage": {"in": 0, "out": 0}}
    sessions[sid] = s
    touch(s)
    return s


def meta(s: dict) -> dict:
    return {k: s.get(k) for k in ("id", "title", "created", "updated", "model", "mode", "repo", "thinking", "effort", "review", "model_mode", "model_roles")} | {
        "running": bool(s.get("_running")), "n": len(s["messages"])}


def touch(s: dict):
    if s.get("_ephemeral"):
        return
    s["updated"] = time.time()
    _dirty[s["id"]] = time.time()
    _ensure_flusher()


def _clean(s: dict) -> dict:
    return {k: v for k, v in s.items() if not k.startswith("_")}


def _save_local(s: dict):
    SESS_DIR.mkdir(parents=True, exist_ok=True)
    (SESS_DIR / f"{s['id']}.json").write_text(json.dumps(_clean(s)), encoding="utf-8")


def _ensure_flusher():
    global _flush_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _flush_task is None or _flush_task.done():
        _flush_task = loop.create_task(_flusher())


async def _flusher():
    while _dirty:
        await asyncio.sleep(3)
        await flush()


async def flush():
    """Write dirty sessions to disk and GitHub."""
    ids = [i for i, t in list(_dirty.items()) if time.time() - t >= 2.5] or []
    for sid in ids:
        _dirty.pop(sid, None)
        s = sessions.get(sid)
        if not s:
            continue
        _save_local(s)
        try:
            from . import db
            if db.enabled(): await db.session_put(sid, _clean(s))
        except Exception as e:
            remote_status["error"] = f"turso session save: {e}"[:200]
        await _remote_put(sid, s)


async def flush_all():
    for sid in list(_dirty):
        _dirty.pop(sid, None)
        s = sessions.get(sid)
        if s:
            _save_local(s)
            try:
                from . import db
                if db.enabled(): await db.session_put(sid, _clean(s))
            except Exception as e:
                remote_status["error"] = f"turso session save: {e}"[:200]
            await _remote_put(sid, s)


async def _remote_put(sid: str, s: dict):
    if not remote_status["enabled"]:
        return
    path = f"sessions/{sid}.json"
    url = f"{API}/repos/{config.STATE_REPO}/contents/{path}"
    body = {"message": f"chat {sid[:8]}", "content": base64.b64encode(
        json.dumps(_clean(s)).encode()).decode()}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            for _ in range(2):
                if sid in _shas:
                    body["sha"] = _shas[sid]
                r = await c.put(url, headers=_gh_headers(), json=body)
                if r.status_code in (200, 201):
                    _shas[sid] = r.json()["content"]["sha"]
                    return
                if r.status_code in (409, 422):      # stale/missing sha: refetch and retry
                    g = await c.get(url, headers=_gh_headers())
                    if g.status_code == 200:
                        _shas[sid] = g.json()["sha"]
                        continue
                    _shas.pop(sid, None)
                    body.pop("sha", None)
                    continue
                remote_status["error"] = f"PUT {r.status_code}: {r.text[:200]}"
                return
    except Exception as e:  # noqa: BLE001
        remote_status["error"] = f"{type(e).__name__}: {e}"


async def delete_session(sid: str):
    sessions.pop(sid, None)
    _dirty.pop(sid, None)
    (SESS_DIR / f"{sid}.json").unlink(missing_ok=True)
    try:
        from . import db
        if db.enabled(): await db.session_delete(sid)
    except Exception as e:
        remote_status["error"] = f"turso session delete: {e}"[:200]
    if remote_status["enabled"] and sid in _shas:
        url = f"{API}/repos/{config.STATE_REPO}/contents/sessions/{sid}.json"
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await c.request("DELETE", url, headers=_gh_headers(),
                                json={"message": f"delete {sid[:8]}", "sha": _shas.pop(sid)})
        except Exception:  # noqa: BLE001
            pass


async def init():
    """Load sessions from local disk, then from the GitHub state repo (creating it if needed)."""
    SESS_DIR.mkdir(parents=True, exist_ok=True)
    for f in SESS_DIR.glob("*.json"):
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
            sessions[s["id"]] = s
        except Exception:  # noqa: BLE001
            pass
    try:
        from . import db
        if db.enabled():
            for sid, s in (await db.sessions_load()).items():
                if sid not in sessions or s.get("updated", 0) > sessions[sid].get("updated", 0): sessions[sid] = s
    except Exception as e:
        remote_status["error"] = f"turso session load: {e}"[:200]
    if not (config.GITHUB_TOKEN and config.STATE_REPO):
        return
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(f"{API}/repos/{config.STATE_REPO}", headers=_gh_headers())
            if r.status_code == 404:
                name = config.STATE_REPO.split("/", 1)[1]
                r = await c.post(f"{API}/user/repos", headers=_gh_headers(), json={
                    "name": name, "private": True, "auto_init": True,
                    "description": "Chat history for my mobile agent"})
                if r.status_code not in (200, 201):
                    remote_status["error"] = f"could not create state repo: {r.status_code} {r.text[:200]}"
                    return
            elif r.status_code != 200:
                remote_status["error"] = f"state repo check failed: {r.status_code}"
                return
            remote_status["enabled"] = True
            lst = await c.get(f"{API}/repos/{config.STATE_REPO}/contents/sessions", headers=_gh_headers())
            if lst.status_code != 200:
                return
            sem = asyncio.Semaphore(8)

            async def load(item):
                async with sem:
                    g = await c.get(item["url"], headers={**_gh_headers(), "Accept": "application/vnd.github.raw+json"})
                    if g.status_code == 200:
                        s = g.json()
                        _shas[s["id"]] = item["sha"]
                        if s["id"] not in sessions or s.get("updated", 0) > sessions[s["id"]].get("updated", 0):
                            sessions[s["id"]] = s

            await asyncio.gather(*[load(i) for i in lst.json() if i["name"].endswith(".json")])
    except Exception as e:  # noqa: BLE001
        remote_status["error"] = f"{type(e).__name__}: {e}"
