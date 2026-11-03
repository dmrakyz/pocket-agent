"""HTTP API for the phone app."""
import asyncio
import base64
import hashlib
import hmac
import mimetypes
import html
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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from .browser import browser, BrowserError
from . import agent, app_act, apps, backup, config, evals, forge, instagram, kv, llm, mcp_server, selftest, shortcut, mcp, memory, timectx, oauth, perms, push, routines, store, telegram, tools, usage, workspace, world

mcp_client = mcp.MCP()
agent.MCP = mcp_client
perms.MCP = mcp_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    await store.init()
    await kv.init()
    await push.init()
    routines.start_loop()
    world.start_loop()
    asyncio.create_task(mcp_client.refresh())
    asyncio.create_task(telegram.init())
    asyncio.create_task(instagram.init())
    asyncio.create_task(_warm())
    yield
    await browser.shutdown()
    await usage.flush(force=True)
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
            "fallback_model": await llm.fallback_model(), "modes": perms.MODES, "self_repo": config.SELF_REPO,
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
    return {"id": evals.start_eval(body.get("model") or await llm.default_model(), str(body.get("effort") or "high"), str(body.get("suite") or "standard"), bool(body.get("review")))}


@app.get("/api/eval-history", dependencies=[Depends(auth)])
async def eval_history():
    return {"history": evals.history()}


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
        return {"running": True, "seq": r.seq, "partial": r.partial, "pending": list(r.pending.values()), "subs": list(r.subs.values())}
    return {"running": False, "seq": r.seq if r else 0, "partial": None, "pending": [], "subs": list(r.subs.values()) if r else []}


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
    if "review" in body:
        s["review"] = bool(body["review"])
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
    tzn = body.get("tz")
    if timectx.valid_tz(tzn):
        s["tz"] = tzn
        if kv.get("user_tz") != tzn:
            await kv.put("user_tz", tzn)
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
    forced = False
    if r:
        forced = await agent.force_stop(r, get_session(sid))
    return {"ok": True, "forced": forced}


@app.post("/api/sessions/{sid}/approve", dependencies=[Depends(auth)])
async def approve(sid: str, body: dict):
    get_session(sid)
    r = agent.runs.get(sid)
    fut = r.approvals.get(body.get("id")) if r else None
    if not fut or fut.done():
        raise HTTPException(404, "no pending approval with that id")
    fut.set_result({"allow": bool(body.get("allow")), "always": bool(body.get("always")),
                    "note": str(body.get("note") or "")[:500], "mode": body.get("mode"),
                   "answers": body.get("answers") if isinstance(body.get("answers"), list) else None})
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
    if ".git" in p.relative_to(ws.resolve()).parts:
        raise HTTPException(400, "the .git directory is off-limits")
    return p


KINDS = {"image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}, "html": {".html", ".htm"},
         "model": {".stl", ".obj", ".glb", ".gltf"}, "pdf": {".pdf"}, "audio": {".mp3", ".wav", ".m4a", ".ogg", ".flac"},
         "video": {".mp4", ".webm", ".mov", ".m4v"}, "archive": {".zip", ".tar", ".gz", ".tgz", ".7z"},
         "sheet": {".csv", ".tsv", ".xlsx", ".xls"}, "doc": {".docx", ".doc", ".pptx", ".odt", ".rtf"},
         "text": {".txt", ".md", ".json", ".py", ".js", ".ts", ".css", ".yaml", ".yml", ".toml", ".sh", ".log", ".xml", ".sql", ".c", ".cpp", ".go", ".rs", ".java"}}


def file_kind(p: Path) -> str:
    if p.is_dir():
        return "folder"
    ext = p.suffix.lower()
    return next((k for k, v in KINDS.items() if ext in v), "other")


@app.get("/api/sessions/{sid}/stat", dependencies=[Depends(auth)])
async def stat_paths(sid: str, paths: list[str] = Query(default=[])):
    """Size + type of files the agent embeds in its answers (for the file cards)."""
    s = get_session(sid)
    ws = await workspace.ensure_workspace(s)
    out = []
    for rel in paths[:30]:
        try:
            p = _safe(ws, rel)
        except HTTPException:
            out.append({"path": rel, "exists": False}); continue
        if not p.exists():
            out.append({"path": rel, "exists": False}); continue
        if p.is_dir():
            n = sum(1 for f in p.rglob("*") if f.is_file() and ".git" not in f.relative_to(p).parts)
            out.append({"path": rel, "exists": True, "kind": "folder", "name": p.name or "workspace", "count": n})
        else:
            out.append({"path": rel, "exists": True, "kind": file_kind(p), "name": p.name, "size": p.stat().st_size})
    return {"files": out}


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


# ------------------------------------------------------------------ live browser view (you can watch and take over)
@app.get("/api/sessions/{sid}/browser", dependencies=[Depends(auth)])
async def browser_info(sid: str):
    get_session(sid)
    return {"info": await browser.info(sid)}


@app.get("/api/sessions/{sid}/browser/shot", dependencies=[Depends(auth)])
async def browser_shot(sid: str):
    get_session(sid)
    try:
        async with browser.lock(sid):
            data = await browser.shot(sid)
    except BrowserError:
        return Response(status_code=204)      # nothing open yet: not an error
    except Exception as e:  # noqa: BLE001
        raise HTTPException(409, f"page is busy: {type(e).__name__}")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/sessions/{sid}/browser/stream", dependencies=[Depends(auth)])
async def browser_stream(sid: str, w: int = 0, h: int = 0):
    get_session(sid)
    if w and h:
        await browser.resize(sid, 1000, round(1000 * h / w))

    async def gen():
        async for item in browser.stream(sid):
            yield json.dumps(item, separators=(",", ":")) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.post("/api/sessions/{sid}/browser/input", dependencies=[Depends(auth)])
async def browser_input(sid: str, body: dict):
    """Instant takeover input: no lock, no waiting for the page to settle."""
    get_session(sid)
    kind = body.get("type")
    try:
        if kind == "resize":
            await browser.resize(sid, 1000, round(1000 * float(body["h"]) / float(body["w"])))
        else:
            await browser.input(sid, kind, **{k: v for k, v in body.items() if k != "type"})
    except (BrowserError, KeyError, ValueError, ZeroDivisionError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/sessions/{sid}/browser/act", dependencies=[Depends(auth)])
async def browser_act(sid: str, body: dict):
    get_session(sid)
    kind = body.get("type")
    try:
        async with browser.lock(sid):
            if kind == "goto":
                await browser.open(sid, str(body.get("url", "")))
            elif kind == "click":
                await browser.click(sid, None, body.get("x"), body.get("y"))
            elif kind == "type":
                await browser.type(sid, str(body.get("text", "")), None, bool(body.get("submit")))
            elif kind == "key":
                await browser.press(sid, str(body.get("key", "Enter")))
            elif kind == "scroll":
                await browser.scroll(sid, body.get("direction", "down"), int(body.get("amount") or 0))
            elif kind == "back":
                await browser.back(sid)
            elif kind == "reload":
                await browser.reload(sid)
            elif kind == "close":
                await browser.close(sid)
                return {"info": None}
            else:
                raise HTTPException(400, "unknown action")
    except BrowserError as e:
        raise HTTPException(400, str(e))
    return {"info": await browser.info(sid)}


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


mimetypes.add_type("text/javascript", ".mjs")   # emcc/ES-module output must load as a module script
mimetypes.add_type("application/wasm", ".wasm")


# ------------------------------------------------------------------ live preview of built pages
# The page is served from the workspace under a URL carrying a short-lived token scoped to ONE chat, with a CSP
# sandbox (opaque origin): model-written page JS can't reach the app, its token, or other chats.
def _preview_sig(sid: str, exp: str) -> str:
    return hmac.new(config.APP_SECRET.encode(), f"preview:{sid}:{exp}".encode(), hashlib.sha256).hexdigest()


@app.post("/api/sessions/{sid}/preview-token", dependencies=[Depends(auth)])
async def preview_token(sid: str):
    s = get_session(sid)
    await workspace.ensure_workspace(s)
    exp = str(int(time.time()) + 3600)
    return {"base": f"/preview/{sid}/{exp}.{_preview_sig(sid, exp)}/"}


@app.get("/preview/{sid}/{ptoken}/{path:path}")
async def preview_serve(sid: str, ptoken: str, path: str = ""):
    try:
        exp, sig = ptoken.split(".", 1)
        ok = hmac.compare_digest(sig, _preview_sig(sid, exp)) and int(exp) > time.time()
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        raise HTTPException(403, "preview link expired; reopen it from the app")
    root = workspace.ws_path(sid).resolve()
    target = (root / path).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(403, "outside the workspace")
    if ".git" in target.relative_to(root).parts:
        raise HTTPException(403, "not available")
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        raise HTTPException(404, "no such file in the workspace")
    return FileResponse(target, headers={
        "Content-Security-Policy": "sandbox allow-scripts allow-forms allow-modals allow-popups allow-pointer-lock",
        "Access-Control-Allow-Origin": "*", "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


# ------------------------------------------------------------------ push notifications
@app.get("/api/push/key", dependencies=[Depends(auth)])
async def push_key():
    return {"available": push.AVAILABLE and bool(push.public_key()), "key": push.public_key(), "devices": len(push.subs())}


@app.post("/api/push/subscribe", dependencies=[Depends(auth)])
async def push_subscribe(body: dict):
    sub = body.get("subscription") or {}
    if not sub.get("endpoint") or not (sub.get("keys") or {}).get("p256dh"):
        raise HTTPException(400, "not a push subscription")
    await push.subscribe(sub)
    return {"ok": True, "devices": len(push.subs())}


@app.post("/api/push/unsubscribe", dependencies=[Depends(auth)])
async def push_unsubscribe(body: dict):
    await push.unsubscribe(body.get("endpoint", ""))
    return {"ok": True, "devices": len(push.subs())}


@app.post("/api/push/test", dependencies=[Depends(auth)])
async def push_test():
    n = await push.notify("Pocket Agent", "Notifications work.", tag="test", force=True)
    return {"sent": n}


@app.post("/api/presence", dependencies=[Depends(auth)])
async def presence(body: dict):
    push.set_presence(bool(body.get("visible")))
    return {"ok": True}


# ------------------------------------------------------------------ routines (scheduled / webhook runs)
@app.get("/api/routines", dependencies=[Depends(auth)])
async def routines_list():
    return {"routines": [routines.public(r) for r in routines.all_routines()]}


@app.post("/api/routines", dependencies=[Depends(auth)])
async def routines_create(body: dict):
    if not (body.get("prompt") or "").strip():
        raise HTTPException(400, "a routine needs a prompt")
    rs = routines.all_routines()
    if len(rs) >= 30:
        raise HTTPException(400, "too many routines (30 max)")
    r = routines.clean(body)
    rs.append(r)
    await routines.save(rs)
    return routines.public(r)


@app.patch("/api/routines/{rid}", dependencies=[Depends(auth)])
async def routines_patch(rid: str, body: dict):
    rs = routines.all_routines()
    i = next((i for i, r in enumerate(rs) if r["id"] == rid), None)
    if i is None:
        raise HTTPException(404, "no such routine")
    if body.get("regenerate_hook"):
        rs[i]["hook"] = routines.secrets.token_urlsafe(12)
    rs[i] = routines.clean(body, rs[i])
    await routines.save(rs)
    return routines.public(rs[i])


@app.delete("/api/routines/{rid}", dependencies=[Depends(auth)])
async def routines_delete(rid: str):
    rs = [r for r in routines.all_routines() if r["id"] != rid]
    await routines.save(rs)
    return {"ok": True}


@app.post("/api/routines/{rid}/run", dependencies=[Depends(auth)])
async def routines_run(rid: str):
    r = routines.find(rid)
    if not r:
        raise HTTPException(404, "no such routine")
    sid = await routines.fire(r, why="you (run now)")
    if not sid:
        raise HTTPException(409, "that routine is still running")
    return {"chat": sid}


_hook_hits: dict[str, list[float]] = {}


@app.api_route("/hook/{rid}/{token}", methods=["GET", "POST"])
async def routine_hook(rid: str, token: str, request: Request):
    r = routines.find(rid)
    if not r or not r.get("hook") or not hmac.compare_digest(token.encode(), r["hook"].encode()):
        raise HTTPException(404, "not found")
    if not r.get("enabled"):
        raise HTTPException(409, "routine is disabled")
    hits = [t for t in _hook_hits.get(rid, []) if time.time() - t < 60]
    if len(hits) >= 6:
        raise HTTPException(429, "too many calls, slow down")
    _hook_hits[rid] = hits + [time.time()]
    payload = (await request.body())[:4000].decode("utf-8", "replace") if request.method == "POST" else str(request.query_params)[:4000]
    sid = await routines.fire(r, payload=payload, why="a webhook")
    return {"started": bool(sid), "chat": sid}


# ------------------------------------------------------------------ connectors (remote MCP servers managed in the app)
def _connector_view():
    out = []
    for srv in mcp_client.servers():
        names = [n.split("__", 2)[-1] for n, t in mcp_client.tools.items() if t["server"]["name"] == srv["name"]]
        out.append({"name": srv["name"], "url": srv["url"], "source": srv.get("source", "app"),
                    "status": mcp_client.report.get(srv["name"], "not connected yet"), "tools": names,
                    "perm": mcp_client.perm(srv["name"]),
                    "reads": sum(1 for n, t in mcp_client.tools.items() if t["server"]["name"] == srv["name"] and mcp_client.kind(n) == "read"),
                    "has_headers": bool(srv.get("headers")), "oauth": bool(srv.get("oauth")),
                    "signed_in": oauth.signed_in(srv["name"]) if srv.get("oauth") else None})
    return out


@app.get("/api/connectors", dependencies=[Depends(auth)])
async def connectors_list():
    return {"connectors": _connector_view()}


@app.post("/api/connectors", dependencies=[Depends(auth)])
async def connectors_add(body: dict):
    name = re.sub(r"[^a-z0-9_-]", "", str(body.get("name", "")).lower())[:24]
    url = str(body.get("url", "")).strip()
    if not name or not url.startswith("https://"):
        raise HTTPException(400, "give it a short name and an https:// URL")
    try:
        tools._check_public(url)
    except tools.ToolError as e:
        raise HTTPException(400, str(e))
    hdr = {}
    if body.get("auth") and not body.get("oauth"):
        hdr["Authorization"] = str(body["auth"])[:2000]
    items = [c for c in (kv.get("connectors", []) or []) if c.get("name") != name]
    items.append({"name": name, "url": url, **({"headers": hdr} if hdr else {}), **({"oauth": True} if body.get("oauth") else {})})
    await kv.put("connectors", items)
    await mcp_client.refresh()
    return {"connectors": _connector_view()}


@app.delete("/api/connectors/{name}", dependencies=[Depends(auth)])
async def connectors_delete(name: str):
    items = [c for c in (kv.get("connectors", []) or []) if c.get("name") != name]
    await kv.put("connectors", items)
    await oauth.forget(name)
    await mcp_client.refresh()
    return {"connectors": _connector_view()}


@app.post("/api/connectors/{name}/login", dependencies=[Depends(auth)])
async def connectors_login(name: str, body: dict):
    srv = next((c for c in (kv.get("connectors", []) or []) if c.get("name") == name and c.get("oauth")), None)
    if not srv:
        raise HTTPException(404, "no such sign-in connector")
    try:
        return {"url": await oauth.start(name, srv["url"], str(body.get("base", "")))}
    except oauth.OAuthError as e:
        raise HTTPException(400, str(e))


@app.get("/oauth/callback")
async def oauth_callback(state: str = "", code: str = "", error: str = ""):
    page = lambda title, text: HTMLResponse(f"<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'><body style='font:18px system-ui;background:#0f1115;color:#eee;padding:40px;text-align:center'><h2>{title}</h2><p>{text}</p>", headers={"Cache-Control": "no-store"})
    if error or not code or not state:
        return page("Sign-in cancelled", "Nothing was connected. You can close this page and try again from the app.")
    try:
        name = await oauth.finish(state, code)
    except oauth.OAuthError as e:
        return page("Sign-in failed", html.escape(str(e)))
    await mcp_client.refresh()
    return page("Connected", f"{html.escape(name)} is signed in. Go back to the app.")


@app.post("/api/connectors/refresh", dependencies=[Depends(auth)])
async def connectors_refresh():
    await mcp_client.refresh()
    return {"connectors": _connector_view()}


# ------------------------------------------------------------------ memory (notes every chat sees) + import
@app.get("/api/memory", dependencies=[Depends(auth)])
async def memory_list():
    return {"notes": memory.notes(), "max": memory.MAX_NOTES}


@app.post("/api/memory", dependencies=[Depends(auth)])
async def memory_add(body: dict):
    r = await memory.save([str(body.get("text", ""))], source="you")
    if r["skipped"]:
        raise HTTPException(400, r["skipped"][0]["why"])
    return {"notes": memory.notes()}


@app.patch("/api/memory/{nid}", dependencies=[Depends(auth)])
async def memory_edit(nid: str, body: dict):
    n, why = await memory.update(nid, str(body.get("text", "")))
    if not n:
        raise HTTPException(400, why)
    return {"notes": memory.notes()}


@app.delete("/api/memory/{nid}", dependencies=[Depends(auth)])
async def memory_delete(nid: str):
    await memory.forget(nid)
    return {"notes": memory.notes()}


@app.delete("/api/memory", dependencies=[Depends(auth)])
async def memory_clear():
    await memory.clear()
    return {"notes": []}


@app.post("/api/memory/import/preview", dependencies=[Depends(auth)])
async def memory_import_preview(body: dict):
    raw = str(body.get("text", ""))[:400_000]
    if not raw.strip():
        raise HTTPException(400, "paste something or choose a file")
    text, needs = memory.extract(raw)
    distilled = False
    if body.get("distill") or needs:
        try:
            cands = await memory.distill(body.get("model") or await llm.default_model(), text)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"the model could not process it ({e}). Try again, or paste the facts as a list.")
        distilled = True
    else:
        cands = memory.split_notes(text)
    items = memory.notes()
    out = []
    for t in cands[:300]:
        why = memory.refusal(t)
        out.append({"text": t, "dup": bool(memory.dupe_of(t, items)), "blocked": why})
    return {"candidates": out, "distilled": distilled, "from_history": needs}


@app.post("/api/memory/import", dependencies=[Depends(auth)])
async def memory_import(body: dict):
    texts = [str(t) for t in (body.get("notes") or [])][:300]
    r = await memory.save(texts, source="import")
    return {"added": len(r["added"]), "skipped": r["skipped"], "notes": memory.notes()}


@app.patch("/api/connectors/{name}", dependencies=[Depends(auth)])
async def connectors_perm(name: str, body: dict):
    perm = str(body.get("perm", ""))
    if perm not in ("ask", "readonly", "trusted"):
        raise HTTPException(400, "perm must be ask, readonly or trusted")
    if name not in {s["name"] for s in mcp_client.servers()}:
        raise HTTPException(404, "no such connector")
    d = kv.get("connector_perms", {}) or {}
    d[name] = perm
    await kv.put("connector_perms", d)
    return {"connectors": _connector_view()}


@app.get("/api/usage", dependencies=[Depends(auth)])
async def usage_view():
    return usage.view(14)


# ------------------------------------------------------------------ telegram
@app.post("/telegram/{secret}")
async def telegram_webhook(secret: str, request: Request):
    """Telegram calls this. Both the path and Telegram's secret header must match, or it is ignored."""
    ok = hmac.compare_digest(secret, telegram.path_secret()) and hmac.compare_digest(
        request.headers.get("x-telegram-bot-api-secret-token", ""), telegram.header_secret())
    if not telegram.enabled() or not ok:
        raise HTTPException(404)
    try:
        update = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(400)
    asyncio.create_task(telegram.handle(update))
    return {"ok": True}


def _tg_view():
    return {"enabled": telegram.enabled(), "bot": telegram.status["bot"], "webhook": telegram.status["webhook"],
            "error": telegram.status["error"], "paired": len(telegram.allowed_ids()), "public_url": config.PUBLIC_URL}


# ------------------------------------------------------------------ app drawer
def _app_sig(app_id: str, exp: str) -> str:
    return hmac.new(config.APP_SECRET.encode(), f"app:{app_id}:{exp}".encode(), hashlib.sha256).hexdigest()


def _need_app(app_id: str) -> dict:
    a = apps.get(app_id)
    if not a:
        raise HTTPException(404, "no such app")
    return a


@app.get("/api/apps", dependencies=[Depends(auth)])
async def apps_list():
    return {"apps": [apps.public(a) for a in sorted(apps.index().values(), key=lambda a: -a.get("updated", 0))]}


@app.post("/api/apps/{app_id}/token", dependencies=[Depends(auth)])
async def app_token(app_id: str):
    _need_app(app_id)
    exp = str(int(time.time()) + 3600)
    return {"url": f"/app-host/{app_id}/{exp}.{_app_sig(app_id, exp)}"}


@app.get("/app-host/{app_id}/{ptoken}")
async def app_host(app_id: str, ptoken: str):
    """The app's page, served with a CSP that sandboxes it (opaque origin) and blocks all network. The token is scoped to this one app."""
    try:
        exp, sig = ptoken.split(".", 1)
        ok = hmac.compare_digest(sig, _app_sig(app_id, exp)) and int(exp) > time.time()
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        raise HTTPException(403, "link expired; reopen the app")
    _need_app(app_id)
    return HTMLResponse(apps.with_sdk(apps.html_of(app_id), app_id), headers={"Content-Security-Policy": apps.CSP, "X-Content-Type-Options": "nosniff",
                                                                       "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@app.patch("/api/apps/{app_id}", dependencies=[Depends(auth)])
async def app_patch(app_id: str, body: dict):
    a = await apps.rename(app_id, str(body.get("name") or ""), str(body.get("icon") or ""))
    if not a:
        raise HTTPException(404, "no such app")
    return apps.public(a)


@app.delete("/api/apps/{app_id}", dependencies=[Depends(auth)])
async def app_delete(app_id: str):
    if not await apps.delete(app_id):
        raise HTTPException(404, "no such app")
    return {"ok": True}


@app.post("/api/apps/{app_id}/ask", dependencies=[Depends(auth)])
async def app_ask(app_id: str, body: dict):
    """What an app's agent.ask() turns into: one answer from the default model, no tools, rate-limited per app."""
    a = _need_app(app_id)
    prompt = str(body.get("prompt") or "")[:apps.MAX_ASK_CHARS]
    if not prompt.strip():
        raise HTTPException(400, "empty prompt")
    if not apps.ask_ok(app_id):
        raise HTTPException(429, f"this app has used its {apps.ASKS_PER_HOUR} AI calls for the hour")
    system = (f"You are the AI engine inside a small phone app called '{a['name']}'. Answer exactly in the format the app asks for, concisely, with no preamble. "
              + str(body.get("system") or "")[:1500])

    async def nop(*_a):
        return None
    try:
        r = await llm.chat(await llm.default_model(), [{"role": "system", "content": system}, {"role": "user", "content": prompt}], [], nop, nop, nop, thinking=False)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"the model failed: {str(e)[:160]}")
    usage.record(await llm.default_model(), (r.get("usage") or {}).get("prompt_tokens", 0), (r.get("usage") or {}).get("completion_tokens", 0))
    return {"text": (r["message"].get("content") or "").strip()}


@app.post("/api/apps/{app_id}/grant", dependencies=[Depends(auth)])
async def app_grant(app_id: str, body: dict):
    _need_app(app_id)
    return apps.public(await apps.grant(app_id, bool(body.get("allow", True))))


@app.post("/api/apps/{app_id}/act", dependencies=[Depends(auth)])
async def app_act_run(app_id: str, body: dict):
    """What an app's agent.act() turns into: the real agent does the task (connectors allowed), once the user has allowed this app."""
    a = _need_app(app_id)
    if not a.get("granted"):
        raise HTTPException(403, "NOT_GRANTED")
    prompt = str(body.get("prompt") or "")[:apps.MAX_ASK_CHARS]
    if not prompt.strip():
        raise HTTPException(400, "empty prompt")
    if not apps.ask_ok(app_id, "act"):
        raise HTTPException(429, f"this app has used its {apps.ACTS_PER_HOUR} agent actions for the hour")
    r = await app_act.act(a, prompt)
    if not r["ok"]:
        raise HTTPException(502, r["text"][:300])
    return {"text": r["text"], "chat": r["chat"]}


@app.get("/api/apps/{app_id}/data", dependencies=[Depends(auth)])
async def app_data(app_id: str):
    _need_app(app_id)
    return {"data": apps.data(app_id)}


@app.put("/api/apps/{app_id}/data/{key}", dependencies=[Depends(auth)])
async def app_data_set(app_id: str, key: str, body: dict):
    _need_app(app_id)
    try:
        await apps.data_set(app_id, key, body.get("value"))
    except apps.AppError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/apps/{app_id}/data/{key}", dependencies=[Depends(auth)])
async def app_data_del(app_id: str, key: str):
    _need_app(app_id)
    await apps.data_del(app_id, key)
    return {"ok": True}


# ------------------------------------------------------------------ saved skills
@app.get("/api/skills", dependencies=[Depends(auth)])
async def skills_list():
    items = kv.get("skills", {}) or {}
    return {"skills": [{"name": n, "description": v.get("description", ""), "instructions": v.get("instructions", ""), "updated": v.get("updated", 0)}
                       for n, v in sorted(items.items(), key=lambda kv_: -kv_[1].get("updated", 0))],
            "builtin": [{"name": n, "description": tools.skill_description(p)} for n, p in sorted(tools.find_skills(Path("/nonexistent")).items()) if tools.BUILTIN_SKILLS in p.parents]}


@app.delete("/api/skills/{name}", dependencies=[Depends(auth)])
async def skills_delete(name: str):
    items = kv.get("skills", {}) or {}
    if name not in items:
        raise HTTPException(404, "no such skill")
    items.pop(name)
    await kv.put("skills", items)
    tools._materialize_user_skills()
    return {"ok": True}


# ------------------------------------------------------------------ toolchain packs
@app.get("/api/packs", dependencies=[Depends(auth)])
async def packs_list():
    from . import packs
    return {"packs": packs.status(), "free_mb": packs.free_mb(), "bash_mem_mb": config.BASH_MEM_MB}


@app.post("/api/packs/{name}", dependencies=[Depends(auth)])
async def packs_install(name: str):
    from . import packs
    try:
        return {"message": await packs.enable(name, wait=1)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/packs/{name}", dependencies=[Depends(auth)])
async def packs_remove(name: str):
    from . import packs
    if not packs.remove(name):
        raise HTTPException(404, "no such pack")
    return {"ok": True}


# ------------------------------------------------------------------ forged tools
@app.get("/api/forge", dependencies=[Depends(auth)])
async def forge_list():
    return {"tools": [forge.public(t) for t in sorted(forge.all_tools().values(), key=lambda t: -t.get("updated", 0))]}


@app.get("/api/forge/{name}", dependencies=[Depends(auth)])
async def forge_one(name: str):
    t = forge.get(name)
    if not t:
        raise HTTPException(404, "no such tool")
    return forge.public(t, code=True)


@app.patch("/api/forge/{name}", dependencies=[Depends(auth)])
async def forge_toggle(name: str, body: dict):
    if not await forge.set_enabled(name, bool(body.get("enabled"))):
        raise HTTPException(404, "no such tool")
    return forge.public(forge.get(name))


@app.delete("/api/forge/{name}", dependencies=[Depends(auth)])
async def forge_delete(name: str):
    if not await forge.remove(name):
        raise HTTPException(404, "no such tool")
    return {"ok": True}


# ------------------------------------------------------------------ back-tap assistant (iOS Shortcut)
@app.post("/api/shortcut")
async def shortcut_run(request: Request, text: str = "", url: str = "", t: str = ""):
    """Called by an iOS Shortcut. Body is either the raw screenshot (Content-Type image/*) or JSON {text, url, image_b64}."""
    h = request.headers.get("authorization", "")
    tok = h[7:] if h.lower().startswith("bearer ") else t
    if not shortcut.check(tok):
        raise HTTPException(401, "bad or revoked shortcut token")
    if not shortcut.rate_ok():
        raise HTTPException(429, "too many requests this hour")
    raw = await request.body()
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(413, "too large")
    image = None
    ct = request.headers.get("content-type", "")
    if ct.startswith("application/json") and raw:
        try:
            j = json.loads(raw)
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "bad JSON")
        text, url = str(j.get("text") or text), str(j.get("url") or url)
        if j.get("image_b64"):
            try:
                image = base64.b64decode(str(j["image_b64"]).split(",")[-1])
            except Exception:  # noqa: BLE001
                raise HTTPException(400, "bad image_b64")
    elif raw:
        image = raw
    try:
        return await shortcut.handle(text=text, url=url, image=image)
    except Exception as e:  # noqa: BLE001
        if image is not None and type(e).__name__ == "UnidentifiedImageError":
            raise HTTPException(400, "that body is not an image")
        raise


@app.get("/api/shortcut/status", dependencies=[Depends(auth)])
async def shortcut_status():
    d = shortcut.state()
    return {"enabled": shortcut.enabled(), "hint": d.get("hint", ""), "uses": d.get("uses", 0), "last": d.get("last", 0), "created": d.get("created", 0)}


@app.post("/api/shortcut/token", dependencies=[Depends(auth)])
async def shortcut_token():
    """Make (or replace) the Shortcut token. It is shown once."""
    return {"token": await shortcut.new_token()}


@app.delete("/api/shortcut", dependencies=[Depends(auth)])
async def shortcut_revoke():
    await shortcut.revoke()
    return {"enabled": False}


# ------------------------------------------------------------------ backup / restore
@app.get("/api/backup", dependencies=[Depends(auth)])
async def backup_download():
    data, counts = await asyncio.to_thread(backup.export_zip)
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="pocket-agent-backup-{time.strftime("%Y-%m-%d")}.zip"', "X-Backup-Counts": json.dumps(counts), "Cache-Control": "no-store"})


@app.post("/api/backup/inspect", dependencies=[Depends(auth)])
async def backup_inspect(request: Request):
    try:
        return await asyncio.to_thread(backup.inspect, await request.body())
    except backup.BackupError as e:
        raise HTTPException(400, str(e))


@app.post("/api/backup/restore", dependencies=[Depends(auth)])
async def backup_restore(request: Request, overwrite: int = 0):
    try:
        return await backup.restore(await request.body(), bool(overwrite))
    except backup.BackupError as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------------ platform check
@app.post("/api/selftest", dependencies=[Depends(auth)])
async def selftest_run():
    return await selftest.run(mcp_client)


# ------------------------------------------------------------------ world engine
def _world(wid: str) -> dict:
    w = world.find(wid)
    if not w:
        raise HTTPException(404, "no such world")
    return w


@app.get("/api/worlds", dependencies=[Depends(auth)])
async def worlds_list():
    return {"worlds": [world.public(w) for w in world.all_worlds()]}


@app.post("/api/worlds", dependencies=[Depends(auth)])
async def worlds_create(body: dict):
    try:
        return world.public(await world.create(body), True)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/worlds/{wid}", dependencies=[Depends(auth)])
async def worlds_get(wid: str):
    return world.public(_world(wid), True)


@app.patch("/api/worlds/{wid}", dependencies=[Depends(auth)])
async def worlds_update(wid: str, body: dict):
    ws = world.all_worlds()
    w = next((x for x in ws if x["id"] == wid), None)
    if not w:
        raise HTTPException(404, "no such world")
    try:
        world.update(w, body)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    await world.save(ws)
    return world.public(w, True)


@app.delete("/api/worlds/{wid}", dependencies=[Depends(auth)])
async def worlds_delete(wid: str):
    await world.save([w for w in world.all_worlds() if w["id"] != wid])
    return {"ok": True}


@app.post("/api/worlds/{wid}/tick", dependencies=[Depends(auth)])
async def worlds_tick(wid: str):
    _world(wid)
    r = await world.tick(wid, "you (run now)")
    return {**r, "world": world.public(_world(wid), True)}


@app.post("/api/worlds/{wid}/say", dependencies=[Depends(auth)])
async def worlds_say(wid: str, body: dict):
    _world(wid)
    if not await world.say(wid, str(body.get("text") or "")):
        raise HTTPException(400, "say something")
    return world.public(_world(wid), True)


# ------------------------------------------------------------------ agent as an MCP server
async def _mcp_serve(request: Request, tok: str):
    h = request.headers.get("authorization", "")
    tok = tok or (h[7:] if h.lower().startswith("bearer ") else "")
    if not mcp_server.check(tok):
        raise HTTPException(401, "bad or revoked MCP token")
    if not mcp_server.rate_ok():
        raise HTTPException(429, "too many requests this hour")
    raw = await request.body()
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(413, "too large")
    try:
        msg = json.loads(raw)
    except Exception:  # noqa: BLE001
        return JSONResponse(mcp_server._err(None, -32700, "parse error"), status_code=400)
    if isinstance(msg, list):
        outs = [r for r in [await mcp_server.handle(m) for m in msg] if r is not None]
        return JSONResponse(outs) if outs else Response(status_code=202)
    out = await mcp_server.handle(msg)
    return JSONResponse(out) if out is not None else Response(status_code=202)


@app.post("/mcp/{tok}")
async def mcp_post_path(request: Request, tok: str):
    return await _mcp_serve(request, tok)


@app.post("/mcp")
async def mcp_post_bearer(request: Request):
    return await _mcp_serve(request, "")


@app.get("/mcp/{tok}")
@app.get("/mcp")
async def mcp_get(tok: str = ""):
    return Response(status_code=405, headers={"Allow": "POST"})   # no server-initiated stream


@app.get("/api/mcp-server/status", dependencies=[Depends(auth)])
async def mcp_server_status():
    d = mcp_server.state()
    return {"enabled": mcp_server.enabled(), "hint": d.get("hint", ""), "uses": d.get("uses", 0), "last": d.get("last", 0)}


@app.post("/api/mcp-server/token", dependencies=[Depends(auth)])
async def mcp_server_token():
    return {"token": await mcp_server.new_token()}


@app.delete("/api/mcp-server", dependencies=[Depends(auth)])
async def mcp_server_revoke():
    await mcp_server.revoke()
    return {"enabled": False}


# ------------------------------------------------------------------ instagram
@app.get("/instagram/{secret}")
async def instagram_verify(secret: str, request: Request):
    """Meta's one-time webhook verification handshake."""
    q = request.query_params
    if (not instagram.enabled() or not hmac.compare_digest(secret, instagram.path_secret()) or q.get("hub.mode") != "subscribe"
            or not hmac.compare_digest(q.get("hub.verify_token", ""), instagram.verify_token())):
        raise HTTPException(404)
    return Response(q.get("hub.challenge", ""), media_type="text/plain")


@app.post("/instagram/{secret}")
async def instagram_webhook(secret: str, request: Request):
    raw = await request.body()
    if not instagram.enabled() or not hmac.compare_digest(secret, instagram.path_secret()) or not instagram.signature_ok(raw, request.headers.get("x-hub-signature-256", "")):
        raise HTTPException(404)
    try:
        payload = json.loads(raw)
    except Exception:  # noqa: BLE001
        raise HTTPException(400)
    asyncio.create_task(instagram.handle(payload))
    return {"ok": True}


def _ig_view(base: str = ""):
    base = (base or config.PUBLIC_URL or "").rstrip("/")
    return {"enabled": instagram.enabled(), "username": instagram.status["username"], "error": instagram.status["error"],
            "paired": len(instagram.allowed_ids()), "callback": f"{base}/instagram/{instagram.path_secret()}" if base else "", "verify_token": instagram.verify_token()}


@app.get("/api/instagram", dependencies=[Depends(auth)])
async def instagram_status(base: str = ""):
    return _ig_view(base)


@app.post("/api/instagram/connect", dependencies=[Depends(auth)])
async def instagram_connect(body: dict):
    """Check the token, subscribe the app to messages, and return a pairing code."""
    if not instagram.enabled():
        raise HTTPException(400, "add INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_APP_SECRET in Render first")
    try:
        me = await instagram.call("GET", "me", params={"fields": "username,user_id"})
        instagram.status["username"] = me.get("username", ""); instagram.status["error"] = ""
        await instagram.subscribe()
    except instagram.IgError as e:
        instagram.status["error"] = str(e)
        raise HTTPException(400, f"Instagram said: {e}")
    return {**_ig_view(str(body.get("base") or "")), "code": instagram.new_code()}


@app.delete("/api/instagram/paired", dependencies=[Depends(auth)])
async def instagram_unpair():
    d = instagram.state(); d["allowed"] = []; await kv.put("instagram", d)
    return _ig_view()


@app.get("/api/telegram", dependencies=[Depends(auth)])
async def telegram_status():
    return _tg_view()


@app.post("/api/telegram/connect", dependencies=[Depends(auth)])
async def telegram_connect(body: dict):
    """Point Telegram at this server and return a pairing code."""
    if not telegram.enabled():
        raise HTTPException(400, "add TELEGRAM_BOT_TOKEN in Render first")
    base = str(body.get("base") or config.PUBLIC_URL).strip().rstrip("/")
    try:
        if not telegram.status["bot"]:
            telegram.status["bot"] = (await telegram.call("getMe")).get("username", "")
        await telegram.set_webhook(base)
    except telegram.TgError as e:
        telegram.status["error"] = str(e)
        raise HTTPException(400, f"Telegram said: {e}")
    d = telegram.state(); d["base"] = base; await kv.put("telegram", d)
    return {**_tg_view(), "code": telegram.new_code()}


@app.delete("/api/telegram/paired", dependencies=[Depends(auth)])
async def telegram_unpair():
    d = telegram.state(); d["allowed"] = []; await kv.put("telegram", d)
    return _tg_view()
