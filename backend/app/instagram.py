"""Instagram DM bridge: talk to the agent from an Instagram professional account's inbox (Meta's Instagram API with Instagram Login).

Flow: Instagram -> POST /instagram/<secret> (webhook, signed with your app secret) -> handle() -> agent run -> watch() replies via the Send API.
Meta only lets an account reply to people who messaged it first, within 24 hours of their last message. Only senders paired with a code from the app are served.
"""
import asyncio
import base64
import hashlib
import hmac
import re
import secrets
import time

import httpx

from . import agent, config, kv, llm, store, telegram

API = config.env("INSTAGRAM_API_BASE", "https://graph.instagram.com/v23.0")   # tests point this at a fake
_transport = None                  # tests inject a fake Graph API
PAIR: dict[str, float] = {}        # pairing code -> expiry
PENDING: dict[str, tuple] = {}     # sender -> (session id, approval id)
SEEN: list[str] = []               # recent message ids (Meta redelivers)
NUDGED: dict[str, float] = {}      # sender -> last time we told an unpaired sender to pair
status = {"username": "", "error": ""}
MAX_BYTES = 950
MAX_CHUNKS = 6
HELP = ("I'm your Pocket Agent. Send a message and I'll work on it.\n\n/new  fresh chat\n/stop  stop what I'm doing\n/mode ask|auto|yolo\n"
        "/status\n/unpair  disconnect this account\n\nApprovals: I'll ask, and you reply YES or NO.")


class IgError(Exception):
    pass


def enabled() -> bool:
    return bool(config.INSTAGRAM_ACCESS_TOKEN and config.INSTAGRAM_APP_SECRET)


def _mac(label: str) -> str:
    return hmac.new(config.APP_SECRET.encode(), ("instagram-" + label).encode(), hashlib.sha256).hexdigest()[:32]


def path_secret() -> str:
    return _mac("path")


def verify_token() -> str:
    return config.INSTAGRAM_VERIFY_TOKEN or _mac("verify")[:20]


def signature_ok(raw: bytes, header: str) -> bool:
    if not config.INSTAGRAM_APP_SECRET or not header.startswith("sha256="):
        return False
    want = hmac.new(config.INSTAGRAM_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header[7:])


def state() -> dict:
    d = kv.get("instagram", {}) or {}
    d.setdefault("allowed", []); d.setdefault("sessions", {}); d.setdefault("mode", "ask")
    return d


def allowed_ids() -> set:
    return {str(x) for x in state()["allowed"]} | config.INSTAGRAM_ALLOWED_IDS


# ---------------------------------------------------------------- Graph API
async def call(method: str, path: str, **kw):
    async with httpx.AsyncClient(timeout=30, transport=_transport) as c:
        r = await c.request(method, f"{API}/{path.lstrip('/')}", headers={"Authorization": "Bearer " + config.INSTAGRAM_ACCESS_TOKEN}, **kw)
    try:
        j = r.json()
    except Exception:  # noqa: BLE001
        raise IgError(f"HTTP {r.status_code}")
    if r.status_code >= 400 or j.get("error"):
        e = j.get("error") or {}
        msg = e.get("message") or f"HTTP {r.status_code}"
        if e.get("code") == 190:
            msg = "the access token is expired or invalid. Generate a new one in the Meta dashboard and update INSTAGRAM_ACCESS_TOKEN."
        elif e.get("code") == 10 or e.get("error_subcode") in (2534022, 2018278):
            msg = "outside Instagram's 24-hour window: that person has to message you first."
        raise IgError(msg)
    return j


def _chunks(text: str) -> list[str]:
    """Split into pieces that fit Instagram's ~1000-byte limit, preferring line and word breaks."""
    out, cur = [], ""
    for line in text.split("\n"):
        while len((line).encode()) > MAX_BYTES:        # one huge line: cut by bytes without splitting a character
            cut = len(line.encode()[:MAX_BYTES].decode("utf8", "ignore"))
            sp = line.rfind(" ", 0, cut)
            cut = sp if sp > cut // 2 else cut
            if cur:
                out.append(cur); cur = ""
            out.append(line[:cut]); line = line[cut:].lstrip()
        if len((cur + "\n" + line).encode()) > MAX_BYTES and cur:
            out.append(cur); cur = line
        else:
            cur = (cur + "\n" + line) if cur else line
    if cur.strip():
        out.append(cur)
    return [c for c in out if c.strip()]


def plain(text: str) -> str:
    """Instagram shows raw text: drop markdown decoration."""
    text = re.sub(r"```[a-zA-Z0-9]*\n?", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 (\2)", text)
    return text.strip()


async def send(to: str, text: str):
    parts = _chunks(plain(text) or "(empty)")
    dropped = len(parts) - MAX_CHUNKS
    for i, ch in enumerate(parts[:MAX_CHUNKS]):
        if i == MAX_CHUNKS - 1 and dropped > 0:
            ch = ch[:MAX_BYTES - 60].rstrip() + f"\n… ({dropped} more part{'s' if dropped > 1 else ''} cut, ask me to continue)"
        try:
            await call("POST", "me/messages", json={"recipient": {"id": to}, "message": {"text": ch}})
        except IgError as e:
            status["error"] = f"send: {e}"
            return


async def typing(to: str):
    try:
        await call("POST", "me/messages", json={"recipient": {"id": to}, "sender_action": "typing_on"})
    except IgError:
        pass


async def subscribe():
    await call("POST", "me/subscribed_apps", params={"subscribed_fields": "messages"})


async def init():
    if not enabled():
        return
    try:
        me = await call("GET", "me", params={"fields": "username,user_id"})
        status["username"] = me.get("username", ""); status["error"] = ""
    except Exception as e:  # noqa: BLE001
        status["error"] = f"{type(e).__name__}: {e}"


def new_code() -> str:
    for k in [k for k, v in PAIR.items() if v < time.time()]:
        PAIR.pop(k, None)
    code = secrets.token_hex(3).upper()
    PAIR[code] = time.time() + 600
    return code


# ---------------------------------------------------------------- incoming
async def handle(payload: dict):
    """One webhook delivery: may carry several messages."""
    try:
        for entry in payload.get("entry") or []:
            me = str(entry.get("id", ""))
            for ev in entry.get("messaging") or []:
                msg = ev.get("message") or {}
                sender = str((ev.get("sender") or {}).get("id", ""))
                if not msg or not sender or sender == me or msg.get("is_echo"):
                    continue
                mid = msg.get("mid") or ""
                if mid:
                    if mid in SEEN:
                        continue
                    SEEN.append(mid); del SEEN[:-300]
                await on_message(sender, msg)
    except Exception as e:  # noqa: BLE001
        status["error"] = f"{type(e).__name__}: {e}"


async def on_message(sender: str, msg: dict):
    text = (msg.get("text") or "").strip()
    low = text.lower().lstrip("/")
    if low.split(" ")[0] == "pair" and text.split(" ")[0].lower().lstrip("/") == "pair":
        return await pair(sender, text)
    if sender not in allowed_ids():
        if time.time() - NUDGED.get(sender, 0) > 3600:
            NUDGED[sender] = time.time()
            await send(sender, "This account is private. In the app open Settings → Instagram, get a pairing code, then send: pair CODE")
        return
    if sender in PENDING and text:
        return await answer_approval(sender, text)
    if text.startswith("/"):
        return await command(sender, text)
    await dispatch(sender, text, msg)


async def pair(sender: str, text: str):
    parts = text.split()
    code = (parts[1] if len(parts) > 1 else "").upper()
    if code and PAIR.get(code, 0) > time.time():
        PAIR.pop(code, None)
        d = state()
        if sender not in [str(x) for x in d["allowed"]]:
            d["allowed"].append(sender)
        await kv.put("instagram", d)
        await send(sender, "Paired. This account can now use your agent.\n\n" + HELP)
    else:
        await send(sender, "That code is wrong or expired. Get a new one in the app (Settings → Instagram).")


def _session(sender: str):
    sid = state()["sessions"].get(sender)
    return store.sessions.get(sid) if sid else None


async def _new_session(sender: str) -> dict:
    d = state()
    s = store.new_session("📷 Instagram", None, await llm.default_model(), d.get("mode", "ask"))
    d["sessions"][sender] = s["id"]
    await kv.put("instagram", d)
    return s


async def command(sender: str, text: str):
    cmd, _, arg = text.partition(" ")
    cmd = cmd.lower(); arg = arg.strip().lower()
    s = _session(sender)
    if cmd in ("/start", "/help"):
        return await send(sender, HELP)
    if cmd == "/new":
        if s and s.get("_running"):
            return await send(sender, "I'm still working. /stop first.")
        await _new_session(sender)
        return await send(sender, "Fresh chat started.")
    if cmd == "/stop":
        run = agent.runs.get(s["id"]) if s else None
        if run and not run.done and run.task and not run.task.done():
            for f in list(run.approvals.values()):
                if not f.done():
                    f.set_result({"allow": False, "note": "stopped"})
            PENDING.pop(sender, None)
            run.task.cancel()
            return await send(sender, "Stopping.")
        return await send(sender, "Nothing is running.")
    if cmd == "/mode":
        if arg not in ("ask", "auto", "yolo"):
            return await send(sender, f"Mode is {state().get('mode', 'ask')}. Use /mode ask, /mode auto or /mode yolo.")
        d = state(); d["mode"] = arg; await kv.put("instagram", d)
        if s:
            s["mode"] = arg
        return await send(sender, f"Mode: {arg}." + (" I'll do everything without asking." if arg == "yolo" else ""))
    if cmd == "/status":
        if not s:
            return await send(sender, "No chat yet. Just send a message.")
        return await send(sender, f"{'Working' if s.get('_running') else 'Idle'} · {s.get('model', '').split(':')[-1]} · mode {s.get('mode')} · {len(s['messages'])} messages")
    if cmd == "/unpair":
        d = state(); d["allowed"] = [c for c in d["allowed"] if str(c) != sender]; await kv.put("instagram", d)
        return await send(sender, "Unpaired. This account can no longer use the agent.")
    await send(sender, "Unknown command.\n\n" + HELP)


async def answer_approval(sender: str, text: str):
    sid, aid = PENDING[sender]
    t = text.strip().lower().rstrip(".!")
    if t not in ("yes", "y", "allow", "ok", "no", "n", "deny", "stop"):
        return await send(sender, "Reply YES to allow or NO to deny.")
    PENDING.pop(sender, None)
    ok = t in ("yes", "y", "allow", "ok")
    run = agent.runs.get(sid)
    fut = run.approvals.get(aid) if run else None
    if fut and not fut.done():
        fut.set_result({"allow": ok, "always": False, "note": "" if ok else "denied from Instagram", "mode": None})
        await send(sender, "→ Allowed" if ok else "→ Denied")
    else:
        await send(sender, "That approval is no longer pending.")


async def dispatch(sender: str, text: str, msg: dict):
    s = _session(sender) or await _new_session(sender)
    if s.get("_running"):
        return await send(sender, "Still working on the last message. Wait for it, or send /stop.")
    images = []
    for a in msg.get("attachments") or []:
        kind, url = a.get("type"), (a.get("payload") or {}).get("url")
        if kind == "image" and url and not images:
            try:
                async with httpx.AsyncClient(timeout=30, transport=_transport) as c:
                    r = await c.get(url)
                if r.status_code == 200 and len(r.content) < 8 * 1024 * 1024:
                    images.append(f"data:{r.headers.get('content-type', 'image/jpeg').split(';')[0]};base64," + base64.b64encode(r.content).decode())
            except httpx.HTTPError:
                pass
        elif kind in ("audio", "video", "file"):
            return await send(sender, "I can only read text and images here. Type it out, or send a picture.")
    if not text and not images:
        return await send(sender, "Send me a message or a picture.")
    run = agent.start(s, text or "(see attached image)", images)
    asyncio.create_task(watch(sender, s, run))


# ---------------------------------------------------------------- outgoing
async def watch(sender: str, s: dict, run):
    async def keep_typing():
        while not run.done:
            await typing(sender)
            await asyncio.sleep(5)
    t = asyncio.create_task(keep_typing())
    errors = []
    try:
        async for ev in run.stream(0):
            if ev.get("type") == "approval":
                PENDING[sender] = (s["id"], ev["id"])
                why = f"\n{ev['reason']}" if ev.get("reason") else ""
                await send(sender, f"Approve?\n{telegram.summarize_call(ev)}{why}\n\nReply YES or NO.")
            elif ev.get("type") == "error":
                errors.append(ev.get("text", "error"))
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        errors.append(f"{type(e).__name__}: {e}")
    finally:
        t.cancel()
        PENDING.pop(sender, None)
    try:
        await run.task
    except BaseException:  # noqa: BLE001
        pass
    last = next((m.get("content") or "" for m in reversed(s["messages"]) if m["role"] == "assistant" and (m.get("content") or "").strip()), "")
    text, files = telegram.split_embeds(last if isinstance(last, str) else "")
    if files:
        text = (text + "\n\n" if text else "") + "(Files I made stay in the app: " + ", ".join(p for p, _, _ in files[:5]) + ")"
    if errors:
        text = (text + "\n\n" if text else "") + "⚠ " + errors[-1]
    await send(sender, text or "Stopped.")
