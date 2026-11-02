"""Telegram bridge: talk to the agent from Telegram. Only chats paired with a code from the app are served.

Flow: Telegram -> POST /telegram/<secret> (webhook) -> handle() -> agent run -> watch() sends the answer, files and approval buttons back.
"""
import asyncio
import base64
import hashlib
import hmac
import re
import secrets
import time
from pathlib import Path

import httpx

from . import agent, config, kv, llm, store, workspace

API = config.env("TELEGRAM_API_BASE", "https://api.telegram.org")   # tests point this at a fake
_transport = None                 # tests inject a fake Telegram
PAIR: dict[str, float] = {}       # pairing code -> expiry
CB: dict[str, tuple] = {}         # callback token -> (session id, approval id)
SEEN: list[int] = []              # recent update ids (Telegram redelivers)
status = {"bot": "", "webhook": "", "error": ""}
MAX_TEXT = 3900
MAX_FILE = 20 * 1024 * 1024
HELP = ("I'm your Pocket Agent. Send a message and I'll work on it.\n\n/new  start a fresh chat\n/stop  stop what I'm doing\n"
        "/mode ask|auto|yolo  how much I may do without asking\n/status  what I'm on\n/unpair  disconnect this Telegram account\n\n"
        "Photos and files you send are given to me. Approvals show up as Allow / Deny buttons.")


class TgError(Exception):
    pass


def token() -> str:
    return config.TELEGRAM_BOT_TOKEN


def enabled() -> bool:
    return bool(token())


def _mac(label: str) -> str:
    return hmac.new(config.APP_SECRET.encode(), ("telegram-" + label).encode(), hashlib.sha256).hexdigest()[:32]


def path_secret() -> str:
    return _mac("path")


def header_secret() -> str:
    return _mac("header")


def state() -> dict:
    d = kv.get("telegram", {}) or {}
    d.setdefault("allowed", []); d.setdefault("sessions", {}); d.setdefault("mode", "ask")
    return d


def allowed_ids() -> set:
    return set(state()["allowed"]) | config.TELEGRAM_ALLOWED_IDS


# ---------------------------------------------------------------- Telegram API
async def call(method: str, files=None, **params):
    async with httpx.AsyncClient(timeout=60, transport=_transport) as c:
        if files:
            r = await c.post(f"{API}/bot{token()}/{method}", data={k: v for k, v in params.items() if v is not None}, files=files)
        else:
            r = await c.post(f"{API}/bot{token()}/{method}", json={k: v for k, v in params.items() if v is not None})
    try:
        j = r.json()
    except Exception:  # noqa: BLE001
        raise TgError(f"HTTP {r.status_code}")
    if not j.get("ok"):
        raise TgError(j.get("description") or f"HTTP {r.status_code}")
    return j.get("result")


async def send(chat: int, text: str, markup: dict | None = None):
    text = (text or "").strip() or "(empty)"
    chunks = [text[i:i + MAX_TEXT] for i in range(0, len(text), MAX_TEXT)]
    for i, ch in enumerate(chunks):
        try:
            await call("sendMessage", chat_id=chat, text=ch, reply_markup=markup if i == len(chunks) - 1 else None, disable_web_page_preview=True)
        except TgError as e:
            status["error"] = f"send: {e}"


async def send_file(chat: int, path: Path, as_photo: bool, caption: str = ""):
    try:
        data = path.read_bytes()
        if as_photo and len(data) <= 10 * 1024 * 1024:
            await call("sendPhoto", files={"photo": (path.name, data)}, chat_id=chat, caption=caption[:900] or None)
        else:
            await call("sendDocument", files={"document": (path.name, data)}, chat_id=chat, caption=caption[:900] or None)
    except (TgError, OSError) as e:
        status["error"] = f"file: {e}"
        await send(chat, f"(couldn't send {path.name}: {e})")


async def download(file_id: str) -> tuple[bytes, str]:
    info = await call("getFile", file_id=file_id)
    if int(info.get("file_size") or 0) > MAX_FILE:
        raise TgError("file is over 20 MB")
    async with httpx.AsyncClient(timeout=60, transport=_transport) as c:
        r = await c.get(f"{API}/file/bot{token()}/{info['file_path']}")
    if r.status_code != 200:
        raise TgError(f"download failed (HTTP {r.status_code})")
    return r.content, info["file_path"].rsplit("/", 1)[-1]


async def set_webhook(base: str):
    base = base.rstrip("/")
    if not base.startswith("https://"):
        raise TgError("Telegram needs a public https:// address for this server")
    await call("setWebhook", url=f"{base}/telegram/{path_secret()}", secret_token=header_secret(),
               allowed_updates=["message", "callback_query"], drop_pending_updates=False)
    status["webhook"] = base; status["error"] = ""


async def init():
    if not enabled():
        return
    try:
        me = await call("getMe")
        status["bot"] = me.get("username", "")
        base = state().get("base") or config.PUBLIC_URL
        if base:
            await set_webhook(base)
    except Exception as e:  # noqa: BLE001
        status["error"] = f"{type(e).__name__}: {e}"


def new_code() -> str:
    for k in [k for k, v in PAIR.items() if v < time.time()]:
        PAIR.pop(k, None)
    code = secrets.token_hex(3).upper()
    PAIR[code] = time.time() + 600
    return code


# ---------------------------------------------------------------- incoming
async def handle(update: dict):
    uid = update.get("update_id")
    if uid is not None:
        if uid in SEEN:
            return
        SEEN.append(uid); del SEEN[:-300]
    try:
        if "callback_query" in update:
            return await on_callback(update["callback_query"])
        msg = update.get("message") or {}
        chat_obj = msg.get("chat") or {}
        if chat_obj.get("type") != "private" or chat_obj.get("id") is None:
            return
        chat = int(chat_obj["id"])
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if text.split("@")[0].split(" ")[0] == "/pair":
            return await pair(chat, text)
        if chat not in allowed_ids():
            return await send(chat, "This bot is private. In the app open Settings → Telegram, get a pairing code, then send /pair CODE here.")
        if text.startswith("/"):
            return await command(chat, text)
        await dispatch(chat, text, msg)
    except Exception as e:  # noqa: BLE001
        status["error"] = f"{type(e).__name__}: {e}"


async def pair(chat: int, text: str):
    parts = text.split()
    code = (parts[1] if len(parts) > 1 else "").upper()
    if code and PAIR.get(code, 0) > time.time():
        PAIR.pop(code, None)
        d = state()
        if chat not in d["allowed"]:
            d["allowed"].append(chat)
        await kv.put("telegram", d)
        await send(chat, "Paired. This chat can now use your agent.\n\n" + HELP)
    else:
        await send(chat, "That code is wrong or expired. Get a new one in the app (Settings → Telegram).")


def _session(chat: int):
    sid = state()["sessions"].get(str(chat))
    return store.sessions.get(sid) if sid else None


async def _new_session(chat: int) -> dict:
    d = state()
    s = store.new_session("✈ Telegram", None, await llm.default_model(), d.get("mode", "ask"))
    d["sessions"][str(chat)] = s["id"]
    await kv.put("telegram", d)
    return s


async def command(chat: int, text: str):
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower(); arg = arg.strip().lower()
    s = _session(chat)
    if cmd in ("/start", "/help"):
        return await send(chat, HELP)
    if cmd == "/new":
        if s and s.get("_running"):
            return await send(chat, "I'm still working. /stop first.")
        await _new_session(chat)
        return await send(chat, "Fresh chat started.")
    if cmd == "/stop":
        run = agent.runs.get(s["id"]) if s else None
        if run and not run.done and run.task and not run.task.done():
            for f in list(run.approvals.values()):
                if not f.done():
                    f.set_result({"allow": False, "note": "stopped"})
            run.task.cancel()
            return await send(chat, "Stopping.")
        return await send(chat, "Nothing is running.")
    if cmd == "/mode":
        if arg not in ("ask", "auto", "yolo"):
            return await send(chat, f"Mode is {state().get('mode', 'ask')}. Use /mode ask, /mode auto or /mode yolo.")
        d = state(); d["mode"] = arg; await kv.put("telegram", d)
        if s:
            s["mode"] = arg
        return await send(chat, f"Mode: {arg}." + (" I'll do everything without asking." if arg == "yolo" else ""))
    if cmd == "/status":
        if not s:
            return await send(chat, "No chat yet. Just send a message.")
        return await send(chat, f"{'Working' if s.get('_running') else 'Idle'} · {s.get('model', '').split(':')[-1]} · mode {s.get('mode')} · {len(s['messages'])} messages")
    if cmd == "/unpair":
        d = state(); d["allowed"] = [c for c in d["allowed"] if c != chat]; await kv.put("telegram", d)
        return await send(chat, "Unpaired. This chat can no longer use the agent.")
    await send(chat, "Unknown command.\n\n" + HELP)


async def dispatch(chat: int, text: str, msg: dict):
    if msg.get("voice") or msg.get("audio") or msg.get("video_note"):
        return await send(chat, "I can't transcribe voice messages yet. Type it, or send it as text.")
    s = _session(chat) or await _new_session(chat)
    if s.get("_running"):
        return await send(chat, "Still working on the last message. Wait for it, or send /stop.")
    images, notes = [], []
    try:
        if msg.get("photo"):
            data, _ = await download(msg["photo"][-1]["file_id"])
            images.append("data:image/jpeg;base64," + base64.b64encode(data).decode())
        doc = msg.get("document")
        if doc:
            data, name = await download(doc["file_id"])
            name = re.sub(r"[^\w.-]", "_", doc.get("file_name") or name)[:80] or "file"
            if (doc.get("mime_type") or "").startswith("image/") and len(data) < 8 * 1024 * 1024 and not images:
                images.append(f"data:{doc['mime_type']};base64," + base64.b64encode(data).decode())
            ws = await workspace.ensure_workspace(s)
            (ws / "uploads").mkdir(exist_ok=True)
            (ws / "uploads" / name).write_bytes(data)
            notes.append(f"[The user attached a file, saved as uploads/{name}]")
    except TgError as e:
        return await send(chat, f"Couldn't get that file: {e}")
    if not text and not images and not notes:
        return await send(chat, "Send me a message, a photo or a file.")
    body = "\n".join(filter(None, [text] + notes)) or "(see attached image)"
    run = agent.start(s, body, images)
    asyncio.create_task(watch(chat, s, run))


# ---------------------------------------------------------------- outgoing
def summarize_call(ev: dict) -> str:
    a = ev.get("args") or {}
    if ev.get("tool") == "exit_plan_mode":
        return "Plan:\n" + str(a.get("plan", ""))[:1500]
    detail = a.get("command") or a.get("path") or a.get("url") or a.get("description") or a.get("query") or ""
    if not detail and a:
        detail = ", ".join(f"{k}={str(v)[:60]}" for k, v in list(a.items())[:3])
    return f"{ev.get('tool')}: {str(detail)[:400]}"


async def ask_approval(chat: int, sid: str, ev: dict):
    tok = secrets.token_hex(4)
    CB[tok] = (sid, ev["id"])
    kb = {"inline_keyboard": [[{"text": "✅ Allow", "callback_data": f"ap:{tok}:1"}, {"text": "🚫 Deny", "callback_data": f"ap:{tok}:0"}]]}
    why = f"\n{ev['reason']}" if ev.get("reason") else ""
    await send(chat, f"Approve?\n{summarize_call(ev)}{why}", kb)


async def on_callback(cq: dict):
    chat = ((cq.get("message") or {}).get("chat") or {}).get("id")
    parts = (cq.get("data") or "").split(":")
    if chat is None or int(chat) not in allowed_ids() or len(parts) != 3 or parts[0] != "ap":
        return
    target = CB.pop(parts[1], None)
    verdict = parts[2] == "1"
    note = "this approval is no longer pending"
    if target:
        run = agent.runs.get(target[0])
        fut = run.approvals.get(target[1]) if run else None
        if fut and not fut.done():
            fut.set_result({"allow": verdict, "always": False, "note": "" if verdict else "denied from Telegram", "mode": None})
            note = "Allowed" if verdict else "Denied"
    try:
        await call("answerCallbackQuery", callback_query_id=cq.get("id"), text=note)
        mid = (cq.get("message") or {}).get("message_id")
        if mid:
            await call("editMessageReplyMarkup", chat_id=chat, message_id=mid, reply_markup={"inline_keyboard": []})
            await call("sendMessage", chat_id=chat, text=f"→ {note}")
    except TgError:
        pass


EMBED_IMG = re.compile(r"!\[([^\]]*)\]\(ws:([^)\s]+)\)")
EMBED_FILE = re.compile(r"\[([^\]]+)\]\(ws:([^)\s]+)\)")


def split_embeds(text: str) -> tuple[str, list]:
    """Replace ws: embeds with plain text and return [(path, is_image, caption)] to send as files."""
    files = []

    def img(m):
        cap = re.sub(r"\s*\|\s*\w+\s*$", "", m.group(1))
        files.append((m.group(2), True, cap))
        return ""

    def fil(m):
        files.append((m.group(2), False, m.group(1)))
        return m.group(1)

    text = EMBED_IMG.sub(img, text)
    text = EMBED_FILE.sub(fil, text)
    return re.sub(r"\n{3,}", "\n\n", text).strip(), files


async def watch(chat: int, s: dict, run):
    async def typing():
        while not run.done:
            try:
                await call("sendChatAction", chat_id=chat, action="typing")
            except TgError:
                pass
            await asyncio.sleep(4)
    t = asyncio.create_task(typing())
    errors = []
    try:
        async for ev in run.stream(0):
            if ev.get("type") == "approval":
                await ask_approval(chat, s["id"], ev)
            elif ev.get("type") == "error":
                errors.append(ev.get("text", "error"))
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        errors.append(f"{type(e).__name__}: {e}")
    finally:
        t.cancel()
    try:
        await run.task
    except BaseException:  # noqa: BLE001
        pass
    last = next((m.get("content") or "" for m in reversed(s["messages"]) if m["role"] == "assistant" and (m.get("content") or "").strip()), "")
    text, files = split_embeds(last if isinstance(last, str) else "")
    if errors:
        text = (text + "\n\n" if text else "") + "⚠ " + errors[-1]
    await send(chat, text or ("Stopped." if not files else ""))
    root = workspace.ws_path(s["id"])
    for path, is_img, cap in files[:8]:
        p = (root / path).resolve()
        if root.resolve() in p.parents and p.is_file():
            await send_file(chat, p, is_img, cap)
