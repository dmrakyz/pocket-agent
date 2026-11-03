"""Permission decisions: allow / ask / deny, per mode. Modes: ask (default), auto (auto-accept edits), plan, yolo."""
import re
import shlex

from . import forge
from .tools import EDITS, READONLY

MODES = ("ask", "auto", "plan", "yolo")
MCP = None   # set by main: used to classify connector tools as read/write

# Never allowed, in any mode (heuristic safety net, not a sandbox).
HARD_DENY = [
    (re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*(/|~|\$HOME|/\*|~/\*)(\s|$)"), "refusing to delete / or your home directory"),
    (re.compile(r":\(\)\s*\{"), "fork bomb"),
    (re.compile(r"\bmkfs\b|\bdd\s+.*of=/dev/"), "disk-destroying command"),
    (re.compile(r"/proc/[^\s]*/environ|/proc/self/environ|\bprintenv\b.*(KEY|TOKEN|PASS)", re.I),
     "reading process environments is blocked (it holds API keys)"),
    (re.compile(r"\bps\s+[a-z]*e[a-z]*w"), "ps with environment output is blocked"),
]

SAFE_CMDS = {"ls", "cat", "head", "tail", "wc", "pwd", "echo", "which", "grep", "rg", "tree", "stat", "file", "du",
             "df", "sort", "uniq", "cut", "date", "whoami", "basename", "dirname", "realpath", "diff", "cmp", "nl",
             "tr", "seq", "true", "false", "uname", "id", "sleep", "jq"}
SAFE_GIT = {"status", "diff", "log", "show", "branch", "rev-parse", "ls-files", "blame", "remote", "describe",
            "shortlog", "tag", "stash"}
NEVER_ALWAYS = {"rm", "sudo", "dd", "chmod", "chown", "bash", "sh", "eval", "curl", "wget", "ssh", "scp", "nc",
                "kill", "pkill", "mv", "env", "xargs", "python", "python3", "node", "perl", "ruby", "git"}


ALWAYS_ASK = {"connector_add", "routine_create", "routine_update", "world_create", "send_email", "forge_tool", "skill_create"}


def _segments(cmd: str) -> list[str] | None:
    """Split a command line on && || ; | and newlines. None if it has constructs we can't judge."""
    if "$(" in cmd or "`" in cmd or "<(" in cmd or re.search(r"(^|[^>&0-9])>{1,2}(?!&)", cmd):
        return None
    return [s.strip() for s in re.split(r"&&|\|\||;|\||\n", cmd) if s.strip()]


def bash_is_safe(cmd: str) -> bool:
    segs = _segments(cmd)
    if not segs:
        return False
    for seg in segs:
        try:
            toks = shlex.split(seg)
        except ValueError:
            return False
        if not toks:
            return False
        first = toks[0]
        if first == "git":
            if len(toks) < 2 or toks[1] not in SAFE_GIT or any(t in ("-D", "-d", "--delete", "-f") for t in toks[2:]):
                return False
        elif first == "find":
            if any(t in ("-exec", "-execdir", "-delete", "-ok", "-fprint") for t in toks):
                return False
        elif first not in SAFE_CMDS:
            return False
    return True


def always_rule(tool: str, args: dict) -> str | None:
    """What 'always allow' should remember for this call (None = not allowed to be remembered)."""
    if tool == "bash":
        try:
            first = shlex.split(args.get("command", ""))[0]
        except (ValueError, IndexError):
            return None
        if first in NEVER_ALWAYS or _segments(args.get("command", "")) is None or len(_segments(args["command"])) > 1:
            return None
        return f"bash:{first}"
    if tool.startswith("mcp__") or tool in EDITS or tool in ("git_sync",):
        return f"tool:{tool}"
    if tool.startswith(forge.PREFIX):
        return f"tool:{tool}"
    return None


def matches_rule(rules: list[str], tool: str, args: dict) -> bool:
    if f"tool:{tool}" in rules:
        return True
    if tool == "bash":
        segs = _segments(args.get("command", ""))
        if not segs or len(segs) > 1:
            return False
        try:
            return f"bash:{shlex.split(segs[0])[0]}" in rules
        except (ValueError, IndexError):
            return False
    return False


def decide(session: dict, tool: str, args: dict) -> tuple[str, str]:
    """Returns ('allow'|'ask'|'deny', reason)."""
    mode = session.get("mode", "ask")
    if tool == "bash":
        cmd = args.get("command", "")
        for rx, why in HARD_DENY:
            if rx.search(cmd):
                return "deny", why
    if tool == "exit_plan_mode":
        return "ask", "Approve this plan?"
    if tool == "forge_tool":   # adds a permanent capability that runs code on the server: the user sees the code and approves every time
        if mode == "plan":
            return "deny", "Plan mode is read-only. Describe the tool you would forge in your plan instead."
        return "ask", "Add this tool?"
    if tool.startswith(forge.PREFIX):
        meta = forge.get(tool[len(forge.PREFIX):]) or {}
        if not meta.get("network"):
            return "allow", ""          # pure computation in a no-network sandbox: safe even in plan mode
        if mode == "plan":
            return "deny", "Plan mode is read-only, and this tool uses the network."
        if mode == "yolo" or matches_rule(session.get("allow", []), tool, args):
            return "allow", ""
        return "ask", ""
    if tool in ("routine_create", "routine_update", "world_create"):   # they set things running on their own: the user sees them first, every time
        if mode == "plan":
            return "deny", "Plan mode is read-only. Describe it in your plan instead."
        return "ask", {"routine_create": "Schedule this routine?", "routine_update": "Change this routine?", "world_create": "Start this world?"}[tool]
    if tool == "skill_create":   # a permanent instruction every future chat will follow: the user reads it first, every time
        if mode == "plan":
            return "deny", "Plan mode is read-only. Describe the skill in your plan instead."
        return "ask", "Save this skill?"
    if tool == "connector_add":   # grants the agent new reach: the user sees the URL and decides every time, in any mode
        if mode == "plan":
            return "deny", "Plan mode is read-only. Propose the connector in your plan instead."
        return "ask", "Add this connector?"
    if tool == "send_email":   # irreversible and outward-facing: asks every time, even in YOLO or with a saved rule
        if mode == "plan":
            return "deny", "Plan mode is read-only. Draft the email in your plan instead."
        return "ask", "Send this email?"
    if tool.startswith("mcp__") and MCP is not None:
        srv, pol, kind = tool.split("__")[1], None, MCP.kind(tool)
        pol = MCP.perm(srv)
        if kind == "read":
            return "allow", ""
        if mode == "plan":
            return "deny", "Plan mode is read-only. Research first, then call exit_plan_mode with your plan."
        if pol == "readonly":
            return "deny", f"The {srv} connector is set to read-only, so this changes nothing there. Tell the user what you wanted to do instead."
        if pol == "trusted" or mode == "yolo" or matches_rule(session.get("allow", []), tool, args):
            return "allow", ""
        return "ask", ""
    if mode == "plan":
        if tool in READONLY:
            return "allow", ""
        if tool == "bash" and bash_is_safe(args.get("command", "")):
            return "allow", ""
        return "deny", "Plan mode is read-only. Research with read/grep/glob, then call exit_plan_mode with your plan."
    if mode == "yolo":
        return "allow", ""
    if tool in READONLY:
        return "allow", ""
    if matches_rule(session.get("allow", []), tool, args):
        return "allow", ""
    if tool == "bash" and bash_is_safe(args.get("command", "")):
        return "allow", ""
    if mode == "auto" and (tool in EDITS or tool == "git_sync"):
        return "allow", ""
    return "ask", ""
