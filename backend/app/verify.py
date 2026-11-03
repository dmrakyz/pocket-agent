"""Verify gate: a coding turn may not end on "done" while the code it changed was never run, or its last check failed.

Works from the message history of the current turn, so it needs no extra state: find the last change to a code file, then look for a
check (a real command that exited 0) after it. Weak models love to say "done" after writing code; this makes them look first."""
import json
import re

from . import config

CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".java", ".kt", ".rb", ".php",
            ".sh", ".swift", ".cs", ".lua", ".html", ".css", ".vue", ".svelte", ".sql"}
WEB_EXT = {".html", ".css", ".js", ".mjs", ".svg", ".vue", ".svelte", ".jsx", ".tsx"}
EDIT_TOOLS = {"write", "edit", "multiedit"}
WEB_CHECKS = {"preview", "browser_read", "browser_act"}
# commands that look at things but do not run or test anything
TRIVIAL = re.compile(r"^\s*(cd\s+\S+\s*(&&|;)\s*)?(ls|cat|echo|pwd|head|tail|wc|grep|rg|find|tree|stat|file|which|mkdir|touch|cp|mv|rm|chmod|sed\s+-n|git\s+(status|diff|log|add|commit|show|branch|checkout|stash))\b")
LONGRUN = re.compile(r"\b(sleep|tail\s+-f)\b")


def _ext(path: str) -> str:
    i = path.rfind(".")
    return path[i:].lower() if i != -1 else ""


def _first_cmd(cmd: str) -> str:
    return (cmd or "").strip().splitlines()[0] if (cmd or "").strip() else ""


def _scan(messages: list):
    start = 0
    for i, m in enumerate(messages):
        if m.get("role") == "user" and not m.get("_synthetic"):
            start = i
    msgs = messages[start:]
    calls: dict[str, tuple[str, dict]] = {}
    last_edit, edited = -1, []
    ok_check, bad_check = -1, -1
    for i, m in enumerate(msgs):
        if m.get("role") == "assistant":
            for c in m.get("tool_calls") or []:
                try:
                    a = json.loads(c["function"].get("arguments") or "{}")
                except (ValueError, TypeError):
                    a = {}
                calls[c["id"]] = (c["function"]["name"], a if isinstance(a, dict) else {})
        elif m.get("role") == "tool":
            name, a = calls.get(m.get("tool_call_id"), (m.get("_name", ""), {}))
            err = bool(m.get("_error"))
            body = m.get("content") or ""
            if name in EDIT_TOOLS and not err and _ext(str(a.get("path", ""))) in CODE_EXT:
                last_edit = i
                edited.append(str(a.get("path")))
            elif name == "bash":
                cmd = _first_cmd(str(a.get("command", "")))
                if TRIVIAL.match(cmd) or a.get("background") or LONGRUN.search(cmd):
                    continue
                if err or re.search(r"\[exit code (?!0\b)\-?\d+\]\s*$", body):
                    bad_check = i
                else:
                    ok_check = i
            elif name in WEB_CHECKS and not err:
                web_only = edited and all(_ext(p) in WEB_EXT for p in edited)
                if web_only:
                    ok_check = i
    return last_edit, edited, ok_check, bad_check


def edited_files(messages: list) -> list:
    """Code files changed in the current turn."""
    return list(dict.fromkeys(_scan(messages)[1]))


def check(messages: list) -> str:
    """'' when fine, otherwise the reason (used as the nudge text)."""
    if not config.VERIFY_GATE:
        return ""
    last_edit, edited, ok_check, bad_check = _scan(messages)
    if last_edit < 0:
        return ""
    if ok_check > last_edit and bad_check < ok_check:
        return ""
    files = ", ".join(dict.fromkeys(edited))[:160]
    if bad_check > last_edit and bad_check > ok_check:
        return (f"[system] Your last check after editing ({files}) FAILED, so the work is not done. Read the error, fix the cause, run it again until it passes, "
                "then answer. If it truly cannot pass here, start your answer with 'Unverified:' and say exactly why.")
    return (f"[system] You changed code ({files}) but never ran anything that checks it after your last edit. Before answering: run the tests, or the program/build itself, "
            "look at the output, and fix what is wrong. If it cannot be run here, start your answer with 'Unverified:' and say why.")
