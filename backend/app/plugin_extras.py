"""The non-skill parts of a Claude plugin: slash commands, sub-agents, hooks, MCP servers.

Parsing is pure (no network, no state) so it is testable offline. Everything here is preview data until the user ticks it.
Hooks come in switched OFF; MCP servers only ever become connectors, which still need their own sign-in.
"""
import hashlib
import json
import re

RESERVED_COMMANDS = {"compact"}
MAX_COMMANDS, MAX_AGENTS, MAX_HOOKS = 40, 20, 20

# Claude Code tool names -> the names used here
TOOLMAP = {"Bash": "bash", "Edit": "edit|multi_edit", "MultiEdit": "multi_edit", "Write": "write", "Read": "read", "Grep": "grep",
           "Glob": "glob", "WebFetch": "web_fetch", "WebSearch": "web_search", "Task": "task", "NotebookEdit": "edit"}
CLAUDE_NAME = {"bash": "Bash", "edit": "Edit", "multi_edit": "MultiEdit", "write": "Write", "read": "Read", "grep": "Grep",
               "glob": "Glob", "web_fetch": "WebFetch", "web_search": "WebSearch", "task": "Task"}
HOOK_EVENTS = ("PreToolUse", "PostToolUse")


def front(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", text, re.S)
    if not m:
        return {}, text.strip()
    fm = {}
    for ln in m.group(1).splitlines():
        k = re.match(r"^([\w-]+):\s*(.*)$", ln)
        if k:
            fm[k.group(1)] = k.group(2).strip().strip("\"'")
    return fm, m.group(2).strip()


def clean_name(s: str, n: int = 32) -> str:
    return re.sub(r"[^a-z0-9-]", "", str(s or "").lower().replace("_", "-").replace(" ", "-"))[:n].strip("-")


def _first_line(body: str) -> str:
    for ln in body.splitlines():
        ln = ln.strip().lstrip("#").strip()
        if ln:
            return ln[:200]
    return ""


def command_item(path: str, text: str) -> dict:
    fm, body = front(text)
    stem = path.rsplit("/", 1)[-1][:-3]
    name = clean_name(stem)
    notes = []
    problem = ""
    if not name or name in RESERVED_COMMANDS:
        problem = "name is reserved or unusable"
    if len(body) < 5 or len(body) > 20000:
        problem = problem or "empty or over 20,000 characters"
    if re.search(r"!`[^`]+`", body):
        notes.append("uses !`shell` lines, which are not run here (they stay as literal text)")
    if "$ARGUMENTS" not in body and not re.search(r"\$[1-9]", body):
        notes.append("takes no arguments; whatever you type after it is appended")
    return {"kind": "command", "path": path, "name": name, "description": fm.get("description") or _first_line(body), "body": body, "problem": problem, "notes": notes}


def agent_item(path: str, text: str) -> dict:
    fm, body = front(text)
    name = clean_name(fm.get("name") or path.rsplit("/", 1)[-1][:-3], 32)
    tools_ = [t.strip() for t in re.split(r"[,\s]+", fm.get("tools", "")) if t.strip()]
    can_edit = any(t in ("Write", "Edit", "MultiEdit", "Bash") for t in tools_)
    problem = "" if name and 20 <= len(body) <= 20000 else "needs a name and a real prompt (20 to 20,000 characters)"
    notes = ["model setting ignored (it uses the default model)"] if fm.get("model") else []
    if can_edit:
        notes.append("its tool list includes edit/shell tools, so it gets edit rights when delegated to")
    return {"kind": "agent", "path": path, "name": name, "description": (fm.get("description") or _first_line(body))[:400],
            "prompt": body, "can_edit": can_edit, "problem": problem, "notes": notes}


def map_matcher(raw: str) -> tuple[str, str]:
    """-> (regex over our tool names, problem)"""
    raw = (raw or "").strip()
    if raw in ("", "*", ".*"):
        return ".*", ""
    parts = []
    for tok in raw.split("|"):
        tok = tok.strip()
        if tok in TOOLMAP:
            parts.append(TOOLMAP[tok])
    if not parts:
        return "", f"matcher {raw!r} names tools that do not exist here"
    return "|".join(parts), ""


def hook_items(text: str, path: str = "hooks.json") -> list[dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    ev = data.get("hooks") if isinstance(data.get("hooks"), dict) else data
    out = []
    if not isinstance(ev, dict):
        return out
    for event, groups in ev.items():
        if not isinstance(groups, list):
            continue
        for g in groups:
            if not isinstance(g, dict):
                continue
            for h in g.get("hooks", []) if isinstance(g.get("hooks"), list) else []:
                if not isinstance(h, dict) or h.get("type", "command") != "command" or not isinstance(h.get("command"), str):
                    continue
                cmd = h["command"].strip()
                rx, problem = map_matcher(g.get("matcher", ""))
                if event not in HOOK_EVENTS:
                    problem = f"event {event} does not exist here (only {', '.join(HOOK_EVENTS)})"
                elif "CLAUDE_PLUGIN_ROOT" in cmd:
                    problem = "calls a script shipped inside the plugin, and plugin files are not installed here"
                out.append({"kind": "hook", "path": path, "event": event, "matcher": g.get("matcher", "") or "*", "regex": rx,
                            "command": cmd[:2000], "problem": problem, "notes": ["switched OFF after install; you turn it on yourself"],
                            "id": "h-" + hashlib.sha1(f"{event}|{g.get('matcher', '')}|{cmd}".encode()).hexdigest()[:8]})
    return out[:40]


def mcp_items(text: str, path: str = ".mcp.json") -> list[dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    servers = data.get("mcpServers") if isinstance(data.get("mcpServers"), dict) else data
    out = []
    if not isinstance(servers, dict):
        return out
    for nm, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        name = re.sub(r"[^a-z0-9_-]", "", str(nm).lower())[:24]
        url = str(spec.get("url") or "")
        notes, problem = [], ""
        if spec.get("command"):
            problem = "runs a local program (stdio), which this server cannot do"
        elif not url.startswith("https://"):
            problem = "no https URL"
        elif spec.get("type") == "sse" or url.rstrip("/").endswith("/sse"):
            problem = "uses the older SSE transport; only streamable HTTP works here"
        if spec.get("headers") or spec.get("env"):
            notes.append("expects credentials; add the token or sign in under Settings → Connectors afterwards")
        if not name:
            problem = problem or "unusable name"
        out.append({"kind": "mcp", "path": path, "name": name, "url": url, "command": " ".join([str(spec.get("command", ""))] + [str(a) for a in spec.get("args", [])]).strip(),
                    "problem": problem, "notes": notes})
    return out[:30]
