"""The agent's tools. Everything runs inside the session's git workspace."""
import asyncio
import difflib
import fnmatch
import ipaddress
import json
import os
import re
import signal
import socket
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

import httpx

from . import config, workspace

LIMIT = config.TOOL_OUTPUT_LIMIT
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".cache"}


class ToolError(Exception):
    pass


def cap(text: str, limit: int = LIMIT) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n\n… [{len(text) - limit} characters truncated] …\n\n" + text[-half:]


class Ctx:
    """What a tool call gets to know about the world."""

    def __init__(self, session: dict, root: Path, emit: Callable[[dict], Awaitable[None]],
                 subagent: Optional[Callable[..., Awaitable[str]]] = None, mcp=None):
        self.session, self.root, self.emit, self.subagent, self.mcp = session, root, emit, subagent, mcp
        self.read_files: set[str] = session.setdefault("_read", set())

    def resolve(self, p: str, must_exist: bool = False) -> Path:
        if not p:
            raise ToolError("path is required")
        path = Path(p)
        path = (path if path.is_absolute() else self.root / path).resolve()
        root = self.root.resolve()
        if path != root and root not in path.parents:
            raise ToolError(f"path {p!r} is outside the workspace ({root})")
        if ".git" in path.relative_to(root).parts:
            raise ToolError("the .git directory is off-limits; use the git_sync tool or bash git commands")
        if must_exist and not path.exists():
            raise ToolError(f"{p!r} does not exist")
        return path

    def rel(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.root.resolve()))
        except ValueError:
            return str(path)


# ---------------------------------------------------------------- schemas
def _fn(name, desc, props, required=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": list(required)}}}


S = {"type": "string"}
BASE_TOOLS = [
    _fn("read", "Read a text file from the workspace with line numbers. Use offset/limit for big files. "
        "You must read a file before editing or overwriting it.",
        {"path": S, "offset": {"type": "integer", "description": "1-based first line"},
         "limit": {"type": "integer", "description": "max lines (default 2000)"}}, ["path"]),
    _fn("write", "Create a file or fully overwrite it. Parent directories are created.",
        {"path": S, "content": S}, ["path", "content"]),
    _fn("edit", "Replace an exact string in a file. old_string must match exactly once unless replace_all is true. "
        "Include enough surrounding context to be unique.",
        {"path": S, "old_string": S, "new_string": S, "replace_all": {"type": "boolean"}},
        ["path", "old_string", "new_string"]),
    _fn("multiedit", "Apply several edits to one file atomically, in order.",
        {"path": S, "edits": {"type": "array", "items": {"type": "object", "properties": {
            "old_string": S, "new_string": S, "replace_all": {"type": "boolean"}},
            "required": ["old_string", "new_string"]}}}, ["path", "edits"]),
    _fn("glob", "Find files by glob pattern (e.g. '**/*.py'), newest first.", {"pattern": S, "path": S}, ["pattern"]),
    _fn("grep", "Search file contents with a regex (ripgrep syntax).",
        {"pattern": S, "path": S, "glob": {"type": "string", "description": "file filter, e.g. '*.py'"},
         "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"]},
         "context": {"type": "integer"}, "case_insensitive": {"type": "boolean"},
         "head_limit": {"type": "integer"}}, ["pattern"]),
    _fn("bash", "Run a shell command in the workspace root. Default timeout 120s. Set background=true for "
        "long-running processes (dev servers, watchers) and poll with bash_output. Network works but secrets are "
        "not in the environment. To publish work use git_sync, not git push.",
        {"command": S, "timeout": {"type": "integer", "description": "seconds, max 600"},
         "background": {"type": "boolean"}, "description": S}, ["command"]),
    _fn("bash_output", "Read new output from a background bash job.", {"job_id": S}, ["job_id"]),
    _fn("kill_bash", "Stop a background bash job.", {"job_id": S}, ["job_id"]),
    _fn("web_fetch", "Fetch a URL and return it as readable text.", {"url": S}, ["url"]),
    _fn("web_search", "Search the web. Returns titles, URLs and snippets.",
        {"query": S, "max_results": {"type": "integer"}}, ["query"]),
    _fn("todo_write", "Maintain your task list for multi-step work. Send the FULL list every time. "
        "Exactly one item should be in_progress.",
        {"todos": {"type": "array", "items": {"type": "object", "properties": {
            "content": S, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}},
            "required": ["content", "status"]}}}, ["todos"]),
    _fn("task", "Delegate a self-contained task to a sub-agent with fresh context; it returns only its final report. "
        "Emit SEVERAL task calls in one message to run them in parallel. Optionally pick a cheaper/faster model "
        "(e.g. 'lightning') for easy work. Sub-agents are read-only unless can_edit is true (then give parallel "
        "agents disjoint files).",
        {"description": S, "prompt": S, "model": {"type": "string", "description": "optional, e.g. 'lightning' or provider:model-id"},
         "can_edit": {"type": "boolean"}}, ["description", "prompt"]),
    _fn("share_file", "Offer a workspace file or folder to the user as a download (folders are zipped).",
        {"path": S, "description": S}, ["path"]),
    _fn("skill", "Load the full instructions of a named skill listed in your system prompt.", {"name": S}, ["name"]),
    _fn("git_sync", "Commit all workspace changes and push them to the session's GitHub branch. "
        "Do this after finishing meaningful work.", {"message": S}, []),
    _fn("create_pr", "Open a GitHub pull request from this session's branch (repo sessions only).",
        {"title": S, "body": S, "base": {"type": "string", "description": "default: repo default branch"}},
        ["title"]),
    _fn("exit_plan_mode", "Plan mode only: present your finished plan to the user for approval.",
        {"plan": S}, ["plan"]),
]
READONLY = {"read", "glob", "grep", "web_fetch", "web_search", "todo_write", "bash_output", "skill", "task", "share_file"}
EDITS = {"write", "edit", "multiedit"}
SUBAGENT_TOOLS = {"read", "glob", "grep", "bash", "bash_output", "kill_bash", "web_fetch", "web_search"}
SUBAGENT_EDIT_TOOLS = SUBAGENT_TOOLS | EDITS


def schemas(session: dict, mcp=None, only: Optional[set] = None, plan_mode: bool = False) -> list[dict]:
    out = []
    for t in BASE_TOOLS:
        n = t["function"]["name"]
        if only is not None and n not in only:
            continue
        if n == "exit_plan_mode" and not plan_mode:
            continue
        if n == "create_pr" and not session.get("repo"):
            continue
        out.append(t)
    if mcp and only is None:
        out.extend(mcp.schemas())
    return out


# ---------------------------------------------------------------- file tools
def _numbered(text: str, start: int = 1) -> str:
    return "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(text.splitlines(), start))


def _is_binary(b: bytes) -> bool:
    return b"\x00" in b[:4096]


def _diff(old: str, new: str, name: str) -> str:
    d = list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{name}", f"b/{name}", lineterm="", n=2))
    if len(d) > 300:
        d = d[:300] + [f"… diff truncated ({len(d) - 300} more lines)"]
    return "\n".join(d)


async def t_read(ctx: Ctx, a: dict):
    p = ctx.resolve(a["path"], must_exist=True)
    if p.is_dir():
        names = sorted(os.listdir(p))
        return "\n".join(n + ("/" if (p / n).is_dir() else "") for n in names[:500]) or "(empty directory)"
    raw = p.read_bytes()
    if _is_binary(raw):
        return f"(binary file, {len(raw)} bytes)"
    text = raw.decode("utf-8", "replace")
    lines = text.splitlines()
    off = max(1, int(a.get("offset") or 1))
    lim = int(a.get("limit") or 2000)
    chunk = lines[off - 1: off - 1 + lim]
    ctx.read_files.add(str(p))
    out = _numbered("\n".join(chunk), off)
    if off - 1 + lim < len(lines):
        out += f"\n… ({len(lines) - (off - 1 + lim)} more lines; use offset={off + lim})"
    return cap(out) if out else "(empty file)"


async def t_write(ctx: Ctx, a: dict):
    p = ctx.resolve(a["path"])
    old = ""
    if p.exists():
        if str(p) not in ctx.read_files:
            raise ToolError("file exists and you haven't read it yet. read it first (or choose a new filename).")
        old = p.read_text("utf-8", "replace")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(a["content"], encoding="utf-8")
    ctx.read_files.add(str(p))
    name = ctx.rel(p)
    return {"text": f"{'Overwrote' if old else 'Created'} {name} ({len(a['content'].splitlines())} lines)",
            "diff": _diff(old, a["content"], name)}


def _apply_edit(content: str, old: str, new: str, replace_all: bool, name: str) -> str:
    if old == new:
        raise ToolError("old_string and new_string are identical")
    n = content.count(old)
    if n == 0:
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        first = next((l.strip() for l in old.splitlines() if l.strip()), "")
        close = difflib.get_close_matches(first, lines, n=3, cutoff=0.6)
        hint = f" Closest lines in the file: {close}" if close else ""
        raise ToolError(f"old_string not found in {name}. It must match exactly, including whitespace.{hint}")
    if n > 1 and not replace_all:
        raise ToolError(f"old_string appears {n} times in {name}. Add more surrounding context or set replace_all.")
    return content.replace(old, new) if replace_all else content.replace(old, new, 1)


async def t_edit(ctx: Ctx, a: dict):
    p = ctx.resolve(a["path"], must_exist=True)
    if str(p) not in ctx.read_files:
        raise ToolError("read the file before editing it.")
    old = p.read_text("utf-8")
    new = _apply_edit(old, a["old_string"], a["new_string"], bool(a.get("replace_all")), ctx.rel(p))
    p.write_text(new, encoding="utf-8")
    return {"text": f"Edited {ctx.rel(p)}", "diff": _diff(old, new, ctx.rel(p))}


async def t_multiedit(ctx: Ctx, a: dict):
    p = ctx.resolve(a["path"], must_exist=True)
    if str(p) not in ctx.read_files:
        raise ToolError("read the file before editing it.")
    old = cur = p.read_text("utf-8")
    for i, e in enumerate(a["edits"], 1):
        try:
            cur = _apply_edit(cur, e["old_string"], e["new_string"], bool(e.get("replace_all")), ctx.rel(p))
        except ToolError as ex:
            raise ToolError(f"edit #{i} failed, nothing was written: {ex}")
    p.write_text(cur, encoding="utf-8")
    return {"text": f"Applied {len(a['edits'])} edits to {ctx.rel(p)}", "diff": _diff(old, cur, ctx.rel(p))}


async def t_glob(ctx: Ctx, a: dict):
    base = ctx.resolve(a.get("path") or ".", must_exist=True)
    hits = []
    for f in base.glob(a["pattern"]):
        if f.is_file() and not (set(f.relative_to(base).parts) & SKIP_DIRS):
            hits.append(f)
    hits.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    out = [ctx.rel(f) for f in hits[:300]]
    return "\n".join(out) + (f"\n… and {len(hits) - 300} more" if len(hits) > 300 else "") if out else "no matches"


async def t_grep(ctx: Ctx, a: dict):
    base = ctx.resolve(a.get("path") or ".", must_exist=True)
    mode = a.get("output_mode") or "content"
    head_limit = int(a.get("head_limit") or 250)
    if workspace.shutil.which("rg"):
        cmd = ["rg", "--no-heading", "--color", "never", "-n", "--max-columns", "300", "--glob", "!.git"]
        if a.get("case_insensitive"):
            cmd.append("-i")
        if mode == "files_with_matches":
            cmd.append("-l")
        elif mode == "count":
            cmd.append("-c")
        elif a.get("context"):
            cmd += ["-C", str(int(a["context"]))]
        if a.get("glob"):
            cmd += ["--glob", a["glob"]]
        cmd += ["-e", a["pattern"], str(base)]
        rc, out = await workspace.run(cmd, cwd=ctx.root, timeout=60)
        if rc == 1:
            return "no matches"
        if rc != 0:
            raise ToolError(out[-400:])
        out = out.replace(str(ctx.root.resolve()) + "/", "")
    else:
        out = _py_grep(ctx, base, a, mode)
    lines = out.splitlines()
    res = "\n".join(lines[:head_limit])
    return cap(res + (f"\n… {len(lines) - head_limit} more lines" if len(lines) > head_limit else "")) or "no matches"


def _py_grep(ctx: Ctx, base: Path, a: dict, mode: str) -> str:
    try:
        rx = re.compile(a["pattern"], re.I if a.get("case_insensitive") else 0)
    except re.error as e:
        raise ToolError(f"bad regex: {e}")
    out = []
    files = [base] if base.is_file() else (f for f in base.rglob("*") if f.is_file())
    for f in files:
        if set(f.parts) & SKIP_DIRS or (a.get("glob") and not fnmatch.fnmatch(f.name, a["glob"])):
            continue
        try:
            raw = f.read_bytes()
            if _is_binary(raw):
                continue
            n = 0
            for i, line in enumerate(raw.decode("utf-8", "replace").splitlines(), 1):
                if rx.search(line):
                    n += 1
                    if mode == "content":
                        out.append(f"{ctx.rel(f)}:{i}:{line[:300]}")
            if n and mode == "files_with_matches":
                out.append(ctx.rel(f))
            elif n and mode == "count":
                out.append(f"{ctx.rel(f)}:{n}")
        except OSError:
            continue
    return "\n".join(out)


# ---------------------------------------------------------------- bash
JOBS: dict[str, dict] = {}
JOB_DIR = Path(config.DATA_DIR) / "jobs"


async def t_bash(ctx: Ctx, a: dict):
    cmd = a["command"]
    timeout = min(int(a.get("timeout") or config.BASH_TIMEOUT), 600)
    env = workspace.clean_env()
    if a.get("background"):
        JOB_DIR.mkdir(parents=True, exist_ok=True)
        jid = "job_" + uuid.uuid4().hex[:6]
        log = JOB_DIR / f"{jid}.log"
        f = open(log, "wb")
        p = await asyncio.create_subprocess_shell(cmd, cwd=str(ctx.root), env=env, stdout=f, stderr=asyncio.subprocess.STDOUT,
                                                  executable="/bin/bash", start_new_session=True)
        JOBS[jid] = {"p": p, "log": log, "pos": 0, "session": ctx.session["id"]}
        await asyncio.sleep(1.5)
        return f"started background job {jid} (pid {p.pid}). Use bash_output to read it.\n" + await _job_read(jid)
    p = await asyncio.create_subprocess_shell(cmd, cwd=str(ctx.root), env=env, stdout=asyncio.subprocess.PIPE,
                                              stderr=asyncio.subprocess.STDOUT, executable="/bin/bash",
                                              start_new_session=True)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raise ToolError(f"command timed out after {timeout}s and was killed. For long-running processes use "
                        f"background=true.")
    except asyncio.CancelledError:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raise
    text = out.decode("utf-8", "replace")
    for secret in (config.GITHUB_TOKEN,):
        if secret:
            text = text.replace(secret, "***")
    text = cap(text) or "(no output)"
    return text + (f"\n[exit code {p.returncode}]" if p.returncode else "")


async def _job_read(jid: str) -> str:
    j = JOBS.get(jid)
    if not j:
        raise ToolError(f"unknown job {jid}")
    data = j["log"].read_bytes()
    new = data[j["pos"]:]
    j["pos"] = len(data)
    status = "running" if j["p"].returncode is None else f"exited with code {j['p'].returncode}"
    return f"[{status}]\n" + (cap(new.decode("utf-8", "replace")) or "(no new output)")


async def t_bash_output(ctx: Ctx, a: dict):
    return await _job_read(a["job_id"])


async def t_kill_bash(ctx: Ctx, a: dict):
    j = JOBS.get(a["job_id"])
    if not j:
        raise ToolError("unknown job")
    try:
        os.killpg(j["p"].pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return "killed"


# ---------------------------------------------------------------- web
def _check_public(url: str):
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ToolError("only http(s) URLs are allowed")
    try:
        infos = socket.getaddrinfo(u.hostname, None)
    except socket.gaierror:
        raise ToolError(f"cannot resolve {u.hostname}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ToolError("refusing to fetch a private/internal address")


async def t_web_fetch(ctx: Ctx, a: dict):
    url = a["url"]
    async with httpx.AsyncClient(timeout=30, follow_redirects=False,
                                 headers={"User-Agent": "Mozilla/5.0 (compatible; MobileAgent/1.0)"}) as c:
        for _ in range(6):
            _check_public(url)
            r = await c.get(url)
            if r.is_redirect and r.headers.get("location"):
                url = str(httpx.URL(url).join(r.headers["location"]))
                continue
            break
        else:
            raise ToolError("too many redirects")
    if r.status_code >= 400:
        raise ToolError(f"HTTP {r.status_code}")
    ctype = r.headers.get("content-type", "")
    if "html" in ctype:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "noscript", "svg", "form"]):
            tag.decompose()
        title = (soup.title.string or "").strip() if soup.title else ""
        text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
        return cap(f"# {title}\n{url}\n\n{text}", 20000)
    if "json" in ctype or ctype.startswith("text/"):
        return cap(r.text, 20000)
    return f"(non-text content: {ctype}, {len(r.content)} bytes)"


async def t_web_search(ctx: Ctx, a: dict):
    q, n = a["query"], min(int(a.get("max_results") or 6), 10)
    results = []
    if config.TAVILY_API_KEY:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post("https://api.tavily.com/search", json={
                "api_key": config.TAVILY_API_KEY, "query": q, "max_results": n})
            if r.status_code == 200:
                results = [(x["title"], x["url"], x.get("content", "")) for x in r.json().get("results", [])]
    elif config.BRAVE_API_KEY:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get("https://api.search.brave.com/res/v1/web/search", params={"q": q, "count": n},
                            headers={"X-Subscription-Token": config.BRAVE_API_KEY})
            if r.status_code == 200:
                results = [(x["title"], x["url"], x.get("description", "")) for x in
                           r.json().get("web", {}).get("results", [])]
    if not results:
        def ddg():
            from ddgs import DDGS
            return list(DDGS().text(q, max_results=n))
        try:
            rows = await asyncio.to_thread(ddg)
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"search failed ({type(e).__name__}: {e}). DuckDuckGo may be rate-limiting; "
                            f"retry in a moment or set TAVILY_API_KEY.")
        results = [(x.get("title", ""), x.get("href", ""), x.get("body", "")) for x in rows]
    if not results:
        return "no results"
    return "\n\n".join(f"{i}. {t}\n   {u}\n   {s[:300]}" for i, (t, u, s) in enumerate(results, 1))


# ---------------------------------------------------------------- misc tools
async def t_todo_write(ctx: Ctx, a: dict):
    todos = [{"content": str(t.get("content", ""))[:300], "status": t.get("status", "pending")}
             for t in a.get("todos", [])]
    ctx.session["todos"] = todos
    await ctx.emit({"type": "todos", "todos": todos})
    return "Todo list updated."


def find_skills(root: Path) -> dict[str, Path]:
    out = {}
    for d in (".agent/skills", ".claude/skills"):
        base = root / d
        if base.is_dir():
            for s in sorted(base.iterdir()):
                if (s / "SKILL.md").is_file():
                    out.setdefault(s.name, s / "SKILL.md")
    return out


def skill_description(path: Path) -> str:
    m = re.search(r"^description:\s*(.+)$", path.read_text("utf-8", "replace")[:2000], re.M)
    return m.group(1).strip() if m else ""


async def t_skill(ctx: Ctx, a: dict):
    sk = find_skills(ctx.root)
    if a["name"] not in sk:
        raise ToolError(f"no skill named {a['name']!r}. Available: {', '.join(sk) or 'none'}")
    return cap(sk[a["name"]].read_text("utf-8", "replace"))


async def t_git_sync(ctx: Ctx, a: dict):
    msg = a.get("message") or "agent: update"
    sha = await workspace.commit_all(ctx.root, msg)
    res = await workspace.push(ctx.session, ctx.root)
    return f"committed {sha[:8]}; {res}"


async def t_create_pr(ctx: Ctx, a: dict):
    s = ctx.session
    if not (s.get("repo") and config.GITHUB_TOKEN):
        raise ToolError("create_pr needs a repo session and GITHUB_TOKEN")
    await t_git_sync(ctx, {"message": a["title"]})
    h = {"Authorization": f"Bearer {config.GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    async with httpx.AsyncClient(timeout=30, headers=h) as c:
        base = a.get("base")
        if not base:
            r = await c.get(f"https://api.github.com/repos/{s['repo']}")
            base = r.json().get("default_branch", "main")
        r = await c.post(f"https://api.github.com/repos/{s['repo']}/pulls", json={
            "title": a["title"], "body": a.get("body", ""), "head": workspace.branch_for(s), "base": base})
        if r.status_code not in (200, 201):
            raise ToolError(f"GitHub said {r.status_code}: {r.text[:300]}")
        return f"PR opened: {r.json()['html_url']}"


async def t_task(ctx: Ctx, a: dict):
    if not ctx.subagent:
        raise ToolError("sub-agents are not available here")
    return await ctx.subagent(a["description"], a["prompt"], a.get("model"), bool(a.get("can_edit")))


async def t_share_file(ctx: Ctx, a: dict):
    p = ctx.resolve(a["path"], must_exist=True)
    kind = "folder (zipped on download)" if p.is_dir() else f"{p.stat().st_size} bytes"
    return {"text": f"Shared {ctx.rel(p)} ({kind}). The user can download it from this chat.", "file": ctx.rel(p)}


async def t_exit_plan_mode(ctx: Ctx, a: dict):
    return "Plan approved. You may now edit files and run commands."


HANDLERS: dict[str, Callable[[Ctx, dict], Awaitable[Any]]] = {
    "read": t_read, "write": t_write, "edit": t_edit, "multiedit": t_multiedit, "glob": t_glob, "grep": t_grep,
    "bash": t_bash, "bash_output": t_bash_output, "kill_bash": t_kill_bash, "web_fetch": t_web_fetch,
    "web_search": t_web_search, "todo_write": t_todo_write, "task": t_task, "skill": t_skill,
    "git_sync": t_git_sync, "create_pr": t_create_pr, "exit_plan_mode": t_exit_plan_mode, "share_file": t_share_file,
}


async def execute(ctx: Ctx, name: str, args: dict) -> dict:
    """Run a tool. Always returns {'text': ..., 'error': bool, 'diff'?: ...} and never raises."""
    try:
        if name.startswith("mcp__") and ctx.mcp:
            return {"text": cap(await ctx.mcp.call(name, args)), "error": False}
        h = HANDLERS.get(name)
        if not h:
            return {"text": f"unknown tool {name!r}. Available: {', '.join(HANDLERS)}", "error": True}
        res = await h(ctx, args)
        if isinstance(res, dict):
            return {"error": False, **res}
        return {"text": res, "error": False}
    except ToolError as e:
        return {"text": f"Error: {e}", "error": True}
    except KeyError as e:
        return {"text": f"Error: missing required argument {e}", "error": True}
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        return {"text": f"Error: {type(e).__name__}: {e}", "error": True}
