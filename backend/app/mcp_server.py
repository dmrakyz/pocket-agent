"""The agent as an MCP server: other MCP clients (Claude Desktop, Cursor, another agent) can call your Pocket Agent as a tool.

Streamable HTTP, JSON responses only. Auth is its own revocable token, in the URL (/mcp/<token>) or as a Bearer header.
Tools: ask_agent (run a task, optionally inside an existing chat), list_chats, read_chat. Approvals are auto-denied, like the back-tap assistant.
"""
import asyncio
import hashlib
import secrets
import time

from . import agent, config, kv, llm, shortcut, store

PROTOCOL = "2025-03-26"
WAIT_SECONDS = 100
HITS: list[float] = []
MAX_PER_HOUR = 60
STYLE = ("[Called by another program through MCP. Answer completely but compactly in plain text. No one is here to approve actions, "
         "so use only tools that need no approval; if something needs approval, say what you would do.]\n")

TOOLS = [
    {"name": "ask_agent", "description": "Give the user's Pocket Agent a task or question and get its answer. It can search the web, browse, read and write in its own workspace, and use its connectors. Returns the answer and a chat id you can pass back to continue the conversation.",
     "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string", "description": "The task or question"}, "chat_id": {"type": "string", "description": "Continue this chat (from an earlier answer) instead of starting a new one"}}, "required": ["prompt"]}},
    {"name": "list_chats", "description": "List the most recent chats of the Pocket Agent (id, title, last update).",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "How many, default 10, max 30"}}}},
    {"name": "read_chat", "description": "Read the last messages of one chat as plain text.",
     "inputSchema": {"type": "object", "properties": {"chat_id": {"type": "string"}, "limit": {"type": "integer", "description": "How many messages, default 6, max 20"}}, "required": ["chat_id"]}},
]


def state() -> dict:
    return kv.get("mcp_server", {}) or {}


def _hash(tok: str) -> str:
    return hashlib.sha256((config.APP_SECRET + ":mcpserver:" + tok).encode()).hexdigest()


def enabled() -> bool:
    return bool(state().get("hash"))


def check(tok: str) -> bool:
    h = state().get("hash")
    return bool(h and tok and secrets.compare_digest(_hash(tok), h))


async def new_token() -> str:
    tok = "mcp_" + secrets.token_urlsafe(24)
    await kv.put("mcp_server", {"hash": _hash(tok), "created": int(time.time()), "uses": 0, "last": 0, "hint": tok[:7] + "…" + tok[-3:]})
    return tok


async def revoke():
    await kv.put("mcp_server", {})


def rate_ok() -> bool:
    now = time.time()
    HITS[:] = [t for t in HITS if now - t < 3600]
    if len(HITS) >= MAX_PER_HOUR:
        return False
    HITS.append(now)
    return True


def _ok(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _err(id_, code, msg):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": msg}}


def _text(t: str, bad: bool = False):
    return {"content": [{"type": "text", "text": t}], "isError": bad}


async def handle(msg) -> dict | None:
    """One JSON-RPC message in, one response out (None for notifications)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _err(None, -32600, "invalid request")
    method, id_, p = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if id_ is None:  # notification (initialized, cancelled, ...)
        return None
    if method == "initialize":
        return _ok(id_, {"protocolVersion": p.get("protocolVersion") or PROTOCOL, "capabilities": {"tools": {"listChanged": False}},
                         "serverInfo": {"name": "pocket-agent", "version": "1"}})
    if method == "ping":
        return _ok(id_, {})
    if method == "tools/list":
        return _ok(id_, {"tools": TOOLS})
    if method == "tools/call":
        try:
            return _ok(id_, await call(str(p.get("name") or ""), p.get("arguments") or {}))
        except Exception as e:  # noqa: BLE001
            return _ok(id_, _text(f"Error: {type(e).__name__}: {e}", True))
    return _err(id_, -32601, f"method not found: {method}")


async def call(name: str, a: dict) -> dict:
    d = state(); d["uses"] = d.get("uses", 0) + 1; d["last"] = int(time.time()); await kv.put("mcp_server", d)
    if name == "list_chats":
        n = max(1, min(30, int(a.get("limit") or 10)))
        rows = sorted(store.sessions.values(), key=lambda s: -s.get("updated", 0))[:n]
        return _text("\n".join(f"{s['id']}  {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(s.get('updated', 0)))}  [{s.get('model', '')}]  {s.get('title', '')}" for s in rows) or "No chats yet.")
    if name == "read_chat":
        s = store.sessions.get(str(a.get("chat_id") or ""))
        if not s:
            return _text("No such chat.", True)
        n = max(1, min(20, int(a.get("limit") or 6)))
        out = []
        for m in s["messages"]:
            if m["role"] in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"].strip():
                out.append(f"{m['role']}: {m['content'].strip()[:3000]}")
        return _text("\n\n".join(out[-n:]) or "(empty)")
    if name == "ask_agent":
        prompt = str(a.get("prompt") or "").strip()
        if not prompt:
            return _text("prompt is required", True)
        cid = str(a.get("chat_id") or "")
        if cid:
            s = store.sessions.get(cid)
            if not s:
                return _text("No such chat.", True)
            if s.get("_running"):
                return _text("That chat is busy right now. Try again in a moment.", True)
        else:
            s = store.new_session("⇄ MCP · " + time.strftime("%H:%M"), None, await llm.default_model(), "ask")
            s["_titled"] = True
        run = agent.start(s, (STYLE if not cid else "") + prompt, [])
        waiter = asyncio.create_task(shortcut._deny_approvals(run, s, "another app over MCP"))
        try:
            await asyncio.wait_for(asyncio.shield(run.task), WAIT_SECONDS)
        except asyncio.TimeoutError:
            asyncio.create_task(_finish_later(run, waiter))
            return _text(f"Still working (chat_id {s['id']}). Call read_chat with that id in a minute to see the result.")
        except BaseException:  # noqa: BLE001
            pass
        waiter.cancel()
        ans = shortcut._answer(s, run)
        bad = any(e["type"] == "error" for e in run.events)
        return _text(f"{ans}\n\n[chat_id: {s['id']}]", bad)
    return _text(f"unknown tool {name!r}", True)


async def _finish_later(run, waiter):
    try:
        await run.task
    except BaseException:  # noqa: BLE001
        pass
    waiter.cancel()
