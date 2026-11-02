"""HTTP API for the phone app."""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from . import agent, config, evals, llm, mcp, perms, store, workspace

mcp_client = mcp.MCP()
agent.MCP = mcp_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    await store.init()
    asyncio.create_task(mcp_client.refresh())
    asyncio.create_task(_warm())
    yield
    await store.flush_all()


async def _warm():
    try:
        await llm.list_models()
    except Exception:  # noqa: BLE001
        pass


app = FastAPI(title="Mobile Agent", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.ALLOWED_ORIGINS, allow_methods=["*"], allow_headers=["*"],
                   allow_credentials=False, max_age=3600,
                   expose_headers=["Content-Disposition"])


# ------------------------------------------------------------------ auth
def make_token() -> str:
    exp = int(time.time()) + config.TOKEN_TTL_DAYS * 86400
    sig = hmac.new(config.APP_SECRET.encode(), str(exp).encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def valid_token(tok: str) -> bool:
    try:
        exp, sig = tok.split(".", 1)
        good = hmac.new(config.APP_SECRET.encode(), exp.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, good) and int(exp) > time.time()
    except Exception:  # noqa: BLE001
        return False


def auth(request: Request, token: Optional[str] = Query(None)):
    h = request.headers.get("authorization", "")
    tok = h[7:] if h.lower().startswith("bearer ") else (token or "")
    if not config.APP_PASSWORD or not valid_token(tok):
        raise HTTPException(401, "unauthorized")


_fails: dict[str, list[float]] = {}


@app.get("/health")
async def health():
    return {"ok": True, "t": int(time.time())}


@app.post("/api/login")
async def login(request: Request, body: dict):
    if not config.APP_PASSWORD:
        raise HTTPException(503, "APP_PASSWORD is not set on the server")
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0]
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < 300]
    if len(recent) >= 6:
        raise HTTPException(429, "too many attempts, wait a few minutes")
    if not hmac.compare_digest(str(body.get("password", "")).encode(), config.APP_PASSWORD.encode()):
        _fails[ip] = recent + [now]
        await asyncio.sleep(1)
        raise HTTPException(401, "wrong password")
    return {"token": make_token()}


# ------------------------------------------------------------------ info / models
@app.get("/api/info", dependencies=[Depends(auth)])
async def info():
    return {"version": os.environ.get("RENDER_GIT_COMMIT", "")[:7], "providers": list(config.providers()), "state": store.remote_status | {"repo": config.STATE_REPO},
            "github": bool(config.GITHUB_TOKEN), "git": bool(workspace.shutil.which("git")),
            "rg": bool(workspace.shutil.which("rg")), "default_model": await llm.default_model(),
            "fallback_model": await llm.fallback_model(), "modes": perms.MODES,
            "mcp": {n: True for n in mcp_client.tools}, "search": "tavily" if config.TAVILY_API_KEY else (
                "brave" if config.BRAVE_API_KEY else "duckduckgo")}


@app.get("/api/models", dependencies=[Depends(auth)])
async def models(refresh: bool = False):
    return {"models": await llm.list_models(force=refresh), "default": await llm.default_model()}


@app.post("/api/models/test", dependencies=[Depends(auth)])
async def test_model(body: dict):
    ref = body.get("ref", "")
    t0 = time.time()
    got = []

    async def on_text(t):
        got.append(t)

    async def nop(_):
        return None
    try:
        r = await asyncio.wait_for(llm.chat(ref, [{"role": "user", "content": "Reply with the single word: pong"}], [],
                                            on_text, nop, nop, thinking=False, max_tokens=20), timeout=60)
        return {"ok": True, "ms": int((time.time() - t0) * 1000), "reply": (r["message"].get("content") or "")[:60]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}


@app.post("/api/eval", dependencies=[Depends(auth)])
async def eval_start(body: dict):
    return {"id": evals.start_eval(body.get("model") or await llm.default_model())}


@app.get("/api/eval-latest", dependencies=[Depends(auth)])
async def eval_latest():
    if not evals.EVALS:
        return {"eval": None}
    return {"eval": evals.public(max(evals.EVALS.values(), key=lambda e: e["started"]))}


@app.get("/api/eval/{eid}", dependencies=[Depends(auth)])
async def eval_get(eid: str):
    if eid not in evals.EVALS:
        raise HTTPException(404)
    return evals.public(evals.EVALS[eid])


@app.post("/api/eval/{eid}/stop", dependencies=[Depends(auth)])
async def eval_stop(eid: str):
    if eid not in evals.EVALS:
        raise HTTPException(404)
    evals.stop_eval(eid)
    return {"ok": True}


@app.post("/api/mcp/refresh", dependencies=[Depends(auth)])
async def mcp_refresh():
    return await mcp_client.refresh()


# ------------------------------------------------------------------ sessions
def get_session(sid: str) -> dict:
    s = store.sessions.get(sid)
    if not s:
        raise HTTPException(404, "no such chat")
    return s


@app.get("/api/sessions", dependencies=[Depends(auth)])
async def list_sessions():
    return sorted((store.meta(s) for s in store.sessions.values()), key=lambda m: -m["updated"])


@app.post("/api/sessions", dependencies=[Depends(auth)])
async def create_session(body: dict):
    repo = (body.get("repo") or "").strip().removeprefix("https://github.com/").removesuffix(".git").strip("/") or None
    if repo and repo.count("/") != 1:
        raise HTTPException(400, "repo must look like owner/name")
    mode = body.get("mode") if body.get("mode") in perms.MODES else "ask"
    s = store.new_session(body.get("title") or "New chat", repo, body.get("model") or await llm.default_model(), mode)
    return store.meta(s)


def run_state(sid: str) -> dict:
    r = agent.runs.get(sid)
    if r and not r.done:
        return {"running": True, "seq": r.seq, "partial": r.partial, "pending": list(r.pending.values())}
    return {"running": False, "seq": r.seq if r else 0, "partial": None, "pending": []}


def commands_for(sid: str) -> list[str]:
    root = workspace.ws_path(sid)
    return sorted(agent.list_commands(root)) if root.exists() else []


@app.get("/api/sessions/{sid}", dependencies=[Depends(auth)])
async def get_one(sid: str):
    s = get_session(sid)
    return {"session": store._clean(s), "run": run_state(sid), "commands": commands_for(sid)}


@app.patch("/api/sessions/{sid}", dependencies=[Depends(auth)])
async def patch_session(sid: str, body: dict):
    s = get_session(sid)
    if "title" in body:
        s["title"] = str(body["title"])[:80] or s["title"]
        s["_titled"] = True
    if "model" in body and body["model"]:
        s["model"] = body["model"]
    if body.get("mode") in perms.MODES:
        s["mode"] = body["mode"]
    if "thinking" in body:
        s["thinking"] = bool(body["thinking"])
    if body.get("effort") in ("off", "medium", "high"):
        s["effort"] = body["effort"]
        s["thinking"] = body["effort"] != "off"
    if "autopilot" in body:
        s["autopilot"] = bool(body["autopilot"])
    if body.get("budget"):
        s["budget"] = max(5, min(int(body["budget"]), 2000))
    store.touch(s)
    return store.meta(s)


@app.delete("/api/sessions/{sid}", dependencies=[Depends(auth)])
async def delete_session(sid: str):
    s = get_session(sid)
    r = agent.runs.get(sid)
    if r and r.task and not r.task.done():
        r.task.cancel()
    await store.delete_session(sid)
    workspace.drop_workspace(sid)
    return {"ok": True}


@app.post("/api/sessions/{sid}/messages", dependencies=[Depends(auth)])
async def send_message(sid: str, body: dict):
    s = get_session(sid)
    if s.get("_running"):
        raise HTTPException(409, "the agent is still working on the previous message")
    text = (body.get("text") or "").strip()
    images = [u for u in (body.get("images") or []) if isinstance(u, str) and u.startswith("data:image/")][:4]
    if not text and not images:
        raise HTTPException(400, "empty message")
    if text == "/compact":
        s["_running"] = True
        run = agent.Run(s)
        agent.runs[sid] = run
        try:
            await agent.compact(s, run, force=True)
        finally:
            s["_running"] = False
            run.done = True
        return {"ok": True, "compacted": True}
    agent.start(s, text or "(see attached image)", images)
    return {"ok": True, "seq": agent.runs[sid].seq}


@app.get("/api/sessions/{sid}/events", dependencies=[Depends(auth)])
async def events(sid: str, after: int = 0):
    get_session(sid)
    run = agent.runs.get(sid)

    async def gen():
        if not run:
            yield "data: " + json.dumps({"type": "done", "status": "idle", "seq": after}) + "\n\n"
            return
        async for ev in run.stream(after):
            yield "data: " + json.dumps(ev) + "\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/sessions/{sid}/stop", dependencies=[Depends(auth)])
async def stop(sid: str):
    get_session(sid)
    r = agent.runs.get(sid)
    if r and r.task and not r.task.done():
        for f in list(r.approvals.values()):
            if not f.done():
                f.set_result({"allow": False, "note": "stopped"})
        r.task.cancel()
    return {"ok": True}


@app.post("/api/sessions/{sid}/approve", dependencies=[Depends(auth)])
async def approve(sid: str, body: dict):
    get_session(sid)
    r = agent.runs.get(sid)
    fut = r.approvals.get(body.get("id")) if r else None
    if not fut or fut.done():
        raise HTTPException(404, "no pending approval with that id")
    fut.set_result({"allow": bool(body.get("allow")), "always": bool(body.get("always")),
                    "note": str(body.get("note") or "")[:500], "mode": body.get("mode")})
    return {"ok": True}


@app.post("/api/sessions/{sid}/rewind", dependencies=[Depends(auth)])
async def rewind(sid: str, body: dict):
    s = get_session(sid)
    if s.get("_running"):
        raise HTTPException(409, "stop the agent first")
    i = int(body.get("index", -1))
    if not (0 <= i < len(s["messages"])) or s["messages"][i].get("role") != "user":
        raise HTTPException(400, "pick one of your own messages")
    m = s["messages"][i]
    ws = await workspace.ensure_workspace(s)
    sha = m.get("_checkpoint")
    note = "files not changed (no checkpoint for that message)"
    if sha:
        r = await workspace.rewind(ws, sha)
        note = "files restored" if r == "ok" else f"file restore failed: {r[:200]}"
    content = m.get("content")
    text = content if isinstance(content, str) else (content[0].get("text", "") if content else "")
    s["messages"] = s["messages"][:i]
    store.touch(s)
    return {"ok": True, "text": text, "note": note}


@app.post("/api/sessions/{sid}/sync", dependencies=[Depends(auth)])
async def sync(sid: str):
    s = get_session(sid)
    ws = await workspace.ensure_workspace(s)
    sha = await workspace.commit_all(ws, "manual sync")
    return {"sha": sha[:8], "result": await workspace.push(s, ws)}


def _safe(ws: Path, rel: str) -> Path:
    p = (ws / rel).resolve()
    if p != ws.resolve() and ws.resolve() not in p.parents:
        raise HTTPException(400, "outside workspace")
    return p


@app.get("/api/sessions/{sid}/files", dependencies=[Depends(auth)])
async def files(sid: str, path: str = ""):
    s = get_session(sid)
    ws = await workspace.ensure_workspace(s)
    p = _safe(ws, path)
    if not p.is_dir():
        raise HTTPException(404, "not a directory")
    items = []
    for c in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        if c.name == ".git":
            continue
        items.append({"name": c.name, "dir": c.is_dir(), "size": c.stat().st_size if c.is_file() else 0})
    return {"path": path, "items": items}


@app.get("/api/sessions/{sid}/file", dependencies=[Depends(auth)])
async def file(sid: str, path: str):
    s = get_session(sid)
    ws = await workspace.ensure_workspace(s)
    p = _safe(ws, path)
    if not p.is_file():
        raise HTTPException(404)
    raw = p.read_bytes()
    if b"\x00" in raw[:4096]:
        return {"path": path, "binary": True, "size": len(raw)}
    return {"path": path, "text": raw[:400_000].decode("utf-8", "replace"), "truncated": len(raw) > 400_000}


# ------------------------------------------------------------------ file upload / download
def _clean_name(name: str) -> str:
    name = os.path.basename(name.replace("\\", "/")).strip() or "upload"
    return re.sub(r"[^\w.\- ()+]", "_", name)[:120]


def _extract(archive: Path, dest: Path) -> int:
    """Safe extraction (no zip-slip, no symlinks, size/count caps). Returns number of files."""
    max_bytes, max_files, n, total = 300 * 1024 * 1024, 5000, 0, 0
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                target = (dest / info.filename).resolve()
                if root != target and root not in target.parents:
                    continue                      # zip-slip attempt: skip silently
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    continue                      # symlink
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                total += info.file_size
                n += 1
                if total > max_bytes or n > max_files:
                    raise HTTPException(413, "archive is too large to extract")
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
        return n
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as t:
            members = t.getmembers()
            if len(members) > max_files or sum(m.size for m in members) > max_bytes:
                raise HTTPException(413, "archive is too large to extract")
            t.extractall(dest, filter="data")
        return len(members)
    return 0


@app.put("/api/sessions/{sid}/upload", dependencies=[Depends(auth)])
async def upload(sid: str, request: Request, name: str, extract: bool = True):
    """Raw-body upload of any file into <workspace>/uploads/. Archives are unpacked next to it."""
    s = get_session(sid)
    ws = await workspace.ensure_workspace(s)
    fname = _clean_name(name)
    dest_dir = ws / "uploads"
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / fname
    if dest.exists():
        dest = dest_dir / f"{int(time.time())}-{fname}"
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    size = 0
    with open(dest, "wb") as f:
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"file is larger than {config.MAX_UPLOAD_MB} MB")
            f.write(chunk)
    out = {"path": str(dest.relative_to(ws)), "size": size, "extracted_to": None, "files": 0}
    if extract and (zipfile.is_zipfile(dest) or tarfile.is_tarfile(dest)):
        folder = dest_dir / re.sub(r"(\.tar)?\.[^.]+$", "", dest.name)
        try:
            out["files"] = _extract(dest, folder)
            out["extracted_to"] = str(folder.relative_to(ws))
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            out["extract_error"] = f"{type(e).__name__}: {e}"
    return out


@app.get("/api/sessions/{sid}/download", dependencies=[Depends(auth)])
async def download(sid: str, path: str = ""):
    s = get_session(sid)
    ws = await workspace.ensure_workspace(s)
    p = _safe(ws, path)
    if not p.exists():
        raise HTTPException(404)
    if p.is_file():
        return FileResponse(p, filename=p.name)
    tmp = tempfile.mkdtemp()
    base = (p.name or "workspace")
    archive = Path(tmp) / f"{base}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for f in p.rglob("*"):
            if f.is_file() and ".git" not in f.relative_to(p).parts:
                z.write(f, f.relative_to(p))
    return FileResponse(archive, filename=archive.name, background=BackgroundTask(shutil.rmtree, tmp, True))


@app.exception_handler(Exception)
async def boom(request: Request, exc: Exception):
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500,
                        headers={"Access-Control-Allow-Origin": "*"})
