"""Install skills (SKILL.md folders) from a public GitHub repo. Preview first, save only what the user ticks.

Only the skill part of a Claude plugin ports: SKILL.md plus the text files next to it. Hooks, slash commands,
sub-agents and MCP bundles are ignored. Install is a UI action, never an agent tool: a skill is standing
instructions, so the user decides what goes in.
"""
import asyncio
import posixpath
import re

import httpx

from . import config

TEXT_EXT = {".md", ".txt", ".py", ".js", ".ts", ".json", ".css", ".html", ".yaml", ".yml", ".csv", ".sh", ".toml", ".svg"}
MAX_SKILLS_FOUND = 200
MAX_FILE = 200_000
MAX_SKILL_TOTAL = 1_000_000
MAX_FILES_PER_SKILL = 40
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"

SUSPICIOUS = [
    (r"(?i)(curl|wget)[^\n|]*\|\s*(ba|z)?sh", "pipes a download into a shell"),
    (r"(?i)ignore (all |any )?(previous|prior|above) (instructions|rules)", "tries to override earlier instructions"),
    (r"(?i)do not (tell|inform|mention to) the user|without (telling|informing) the user", "asks to hide things from you"),
    (r"[A-Za-z0-9+/]{300,}={0,2}", "contains a long base64-looking blob"),
    (r"(?i)(send|post|upload|exfiltrat)\w*[^\n]{0,60}(api[_ -]?key|token|password|secret|credentials|\.env)", "talks about sending keys or secrets somewhere"),
    (r"(?i)rm\s+-rf\s+(/|~)", "contains a destructive rm -rf"),
]


class InstallError(Exception):
    pass


def parse_source(src: str) -> tuple[str, str, str | None, str]:
    """-> (owner, repo, ref|None, subpath)"""
    s = (src or "").strip().rstrip("/")
    s = re.sub(r"^(https?://)?(www\.)?github\.com/", "", s)
    s = re.sub(r"\.git$", "", s)
    parts = [p for p in s.split("/") if p]
    if len(parts) < 2 or not re.fullmatch(r"[\w.-]+", parts[0]) or not re.fullmatch(r"[\w.-]+", parts[1]):
        raise InstallError("give a GitHub repo like owner/repo, or a github.com link to a repo or folder")
    owner, repo = parts[0], parts[1]
    ref, sub = None, ""
    if len(parts) >= 4 and parts[2] in ("tree", "blob"):
        ref, sub = parts[3], "/".join(parts[4:])
    elif len(parts) > 2:
        sub = "/".join(parts[2:])
    if ".." in sub.split("/"):
        raise InstallError("bad path")
    if sub.endswith("SKILL.md"):
        sub = posixpath.dirname(sub)
    return owner, repo, ref, sub


def _headers():
    h = {"Accept": "application/vnd.github+json", "User-Agent": "pocket-agent"}
    if config.GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    return h


def parse_skill_md(text: str, fallback_name: str) -> tuple[str, str, str]:
    """frontmatter name/description and the body. Description may be folded (>-) or quoted."""
    name, desc, body = fallback_name, "", text
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", text, re.S)
    if m:
        fm, body = m.group(1), m.group(2)
        nm = re.search(r"^name:\s*(.+)$", fm, re.M)
        if nm:
            name = nm.group(1).strip().strip("\"'")
        dm = re.search(r"^description:\s*(.*)$", fm, re.M)
        if dm:
            first = dm.group(1).strip()
            if first in (">", ">-", "|", "|-", ">+", "|+", ""):
                lines = []
                for ln in fm[dm.end():].splitlines():
                    if ln.startswith((" ", "\t")):
                        lines.append(ln.strip())
                    elif ln.strip():
                        break
                desc = " ".join(lines)
            else:
                desc = first.strip("\"'")
    if not desc:
        for ln in body.splitlines():
            ln = ln.strip().lstrip("#").strip()
            if ln:
                desc = ln[:300]
                break
    return name, desc, body.strip()


def warnings_for(texts: list[str]) -> list[str]:
    out = []
    for pat, why in SUSPICIOUS:
        if any(re.search(pat, t) for t in texts):
            out.append(why)
    return out


async def _get_json(c, url):
    r = await c.get(url)
    if r.status_code == 404:
        raise InstallError("repo, branch or path not found (private repos need GITHUB_TOKEN)")
    if r.status_code in (403, 429):
        raise InstallError("GitHub rate-limited this server; set GITHUB_TOKEN or try again in a few minutes")
    if r.status_code != 200:
        raise InstallError(f"GitHub said {r.status_code}")
    return r.json()


async def resolve(c, owner, repo, ref):
    """-> (ref, commit sha)"""
    if not ref:
        ref = (await _get_json(c, f"{API}/repos/{owner}/{repo}")).get("default_branch", "main")
    d = await _get_json(c, f"{API}/repos/{owner}/{repo}/commits/{ref}")
    return ref, d["sha"]


async def _raw(c, owner, repo, sha, path) -> str | None:
    # contents API at the pinned commit: works for private repos with a token and does not depend on raw.githubusercontent.com
    r = await c.get(f"{API}/repos/{owner}/{repo}/contents/{path}?ref={sha}", headers={"Accept": "application/vnd.github.raw+json"})
    if r.status_code != 200 or len(r.content) > MAX_FILE:
        return None
    try:
        return r.content.decode("utf-8")
    except UnicodeDecodeError:
        return None


async def discover(source: str, sha: str | None = None) -> dict:
    owner, repo, ref, sub = parse_source(source)
    async with httpx.AsyncClient(timeout=30, headers=_headers(), follow_redirects=True) as c:
        if sha:
            ref = sha
        else:
            ref, sha = await resolve(c, owner, repo, ref)
        tree = await _get_json(c, f"{API}/repos/{owner}/{repo}/git/trees/{sha}?recursive=1")
        entries = [e for e in tree.get("tree", []) if e.get("type") == "blob"]
        pre = (sub + "/") if sub else ""
        entries = [e for e in entries if e["path"].startswith(pre) or not sub]
        dirs = sorted({posixpath.dirname(e["path"]) for e in entries if posixpath.basename(e["path"]) == "SKILL.md"})
        # a skill folder nested inside another skill folder belongs to the outer one only if it has no SKILL.md of its own
        dirs = dirs[:MAX_SKILLS_FOUND]
        sem = asyncio.Semaphore(8)

        async def one(d):
            files = []
            total = 0
            for e in entries:
                p = e["path"]
                if p == (d + "/SKILL.md" if d else "SKILL.md"):
                    continue
                if d and not p.startswith(d + "/"):
                    continue
                rel = p[len(d) + 1:] if d else p
                # skip files that belong to a nested skill folder
                if any(o != d and (p.startswith(o + "/")) and (not d or o.startswith(d + "/")) for o in dirs):
                    continue
                if posixpath.splitext(rel)[1].lower() not in TEXT_EXT or e.get("size", 0) > MAX_FILE:
                    continue
                if total + e.get("size", 0) > MAX_SKILL_TOTAL or len(files) >= MAX_FILES_PER_SKILL:
                    continue
                total += e.get("size", 0)
                files.append(rel)
            async with sem:
                text = await _raw(c, owner, repo, sha, (d + "/SKILL.md") if d else "SKILL.md")
                if text is None:
                    return None
                fb = posixpath.basename(d) if d else repo
                name, desc, body = parse_skill_md(text, fb)
                fcont = {}
                for rel in files:
                    t = await _raw(c, owner, repo, sha, (d + "/" + rel) if d else rel)
                    if t is not None:
                        fcont[rel] = t
            warns = warnings_for([body] + list(fcont.values()))
            return {"path": d, "name": name, "description": desc, "instructions": body, "files": fcont, "warnings": warns}

        found = [x for x in await asyncio.gather(*[one(d) for d in dirs]) if x]
        extras = await _extras(c, owner, repo, sha, entries, sem)
    nothing = not found and not any(extras.values())
    return {"owner": owner, "repo": repo, "ref": ref, "sha": sha, "subpath": sub, "skills": found, **extras,
            "note": "Nothing installable found there (no SKILL.md, commands, agents, hooks or MCP servers)." if nothing else ""}


async def _extras(c, owner, repo, sha, entries, sem) -> dict:
    from . import plugin_extras as px
    paths = [e["path"] for e in entries if e.get("size", 0) <= MAX_FILE]
    cm = [p for p in paths if re.search(r"(^|/)commands/[^/]+\.md$", p)][:60]
    ag = [p for p in paths if re.search(r"(^|/)agents/[^/]+\.md$", p)][:40]
    hk = [p for p in paths if re.search(r"(^|/)hooks/hooks\.json$|(^|/)hooks\.json$", p)][:5]
    mc = [p for p in paths if re.search(r"(^|/)\.mcp\.json$", p)][:5]

    async def get(p):
        async with sem:
            return p, await _raw(c, owner, repo, sha, p)
    got = dict(await asyncio.gather(*[get(p) for p in cm + ag + hk + mc]))
    out = {"commands": [], "agents": [], "hooks": [], "mcp": []}
    for p in cm:
        if got.get(p):
            out["commands"].append(px.command_item(p, got[p]))
    for p in ag:
        if got.get(p):
            out["agents"].append(px.agent_item(p, got[p]))
    for p in hk:
        if got.get(p):
            out["hooks"] += px.hook_items(got[p], p)
    for p in mc:
        if got.get(p):
            out["mcp"] += px.mcp_items(got[p], p)
    # hooks that call scripts inside the plugin: fetch the plugin's own text files so they can be read now and run later
    for hk_ in out["hooks"]:
        if "CLAUDE_PLUGIN_ROOT" not in hk_["command"] or hk_["event"] not in px.HOOK_EVENTS or not hk_["regex"]:
            continue
        p = hk_["path"]
        root = p[:-len("/hooks/hooks.json")] if p.endswith("/hooks/hooks.json") else ("" if p == "hooks/hooks.json" else posixpath.dirname(p))
        want = [e for e in entries if (not root or e["path"].startswith(root + "/")) and "/.git/" not in "/" + e["path"] and "node_modules/" not in e["path"]
                and posixpath.splitext(e["path"])[1].lower() in TEXT_EXT and e.get("size", 0) <= MAX_FILE]
        total, pick = 0, []
        for e in want:
            if len(pick) < 60 and total + e.get("size", 0) <= MAX_SKILL_TOTAL:
                total += e.get("size", 0); pick.append(e["path"])
        got2 = dict(await asyncio.gather(*[get(q) for q in pick]))
        pf = {(q[len(root) + 1:] if root else q): t for q, t in got2.items() if t is not None}
        if pf:
            hk_["plugin_files"] = pf
            hk_["plugin_id"] = px.clean_name(f"{owner}-{repo}-{root}", 40) or "plugin"
            hk_["problem"] = ""
            hk_["notes"] = [f"runs a script from the plugin; {len(pf)} plugin file(s) are installed with it (read them below). Switched OFF after install."]
            hk_["preview"] = hk_["command"] + "\n\n" + "\n".join(f"===== {k} =====\n{v[:6000]}" for k, v in list(pf.items())[:12])[:30000]
    for key, field in (("commands", "body"), ("agents", "prompt"), ("hooks", "command")):
        for it in out[key]:
            it["warnings"] = warnings_for([it[field]] + list((it.get("plugin_files") or {}).values()))
    for it in out["mcp"]:
        it["warnings"] = []
    return out


def clean_files(files: dict, allow_skill_md: bool = False) -> dict:
    out, total = {}, 0
    for rel, txt in (files or {}).items():
        rel = str(rel).replace("\\", "/")
        if (rel.startswith("/") or ".." in rel.split("/") or (rel.upper() == "SKILL.MD" and not allow_skill_md) or not re.fullmatch(r"[\w./ -]{1,120}", rel)
                or posixpath.splitext(rel)[1].lower() not in TEXT_EXT or not isinstance(txt, str) or len(txt) > MAX_FILE):
            continue
        total += len(txt)
        if total > MAX_SKILL_TOTAL or len(out) >= MAX_FILES_PER_SKILL:
            break
        out[rel] = txt
    return out
