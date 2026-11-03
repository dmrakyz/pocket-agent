"""One-tap publish of a static folder to GitHub Pages (branch gh-pages of a repo the token owns)."""
import os
import re
import shutil
import tempfile
from pathlib import Path

import httpx

from . import config, workspace

API = os.environ.get("PAGES_GH_API", "https://api.github.com")
GIT_BASE = os.environ.get("PAGES_GIT_BASE", "https://github.com")      # tests point this at a local directory of bare repos
SKIP = {".git", "node_modules", "__pycache__", ".agent"}
MAX_MB = 90


class PagesError(Exception):
    pass


def _size_mb(folder: Path) -> float:
    n = 0
    for dp, dn, fn in os.walk(folder):
        dn[:] = [d for d in dn if d not in SKIP]
        n += sum((Path(dp) / f).stat().st_size for f in fn)
    return n / 1048576


def clean_repo(repo: str, owner: str) -> str:
    repo = (repo or "").strip().strip("/")
    if "/" not in repo:
        repo = f"{owner}/{repo}"
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise PagesError("repo must look like owner/name")
    return repo


def url_for(repo: str) -> str:
    owner, name = repo.split("/")
    return f"https://{owner}.github.io/" + ("" if name.lower() == f"{owner.lower()}.github.io" else name + "/")


async def publish(folder: Path, repo: str) -> str:
    from .tools import ToolError
    if not config.GITHUB_TOKEN:
        raise ToolError("publishing needs GITHUB_TOKEN on the server")
    if _size_mb(folder) > MAX_MB:
        raise ToolError(f"the folder is over {MAX_MB} MB; GitHub Pages sites are limited to about 1 GB but pushes of this size will fail here")
    h = {"Authorization": f"Bearer {config.GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    async with httpx.AsyncClient(timeout=30, headers=h) as c:
        me = await c.get(f"{API}/user")
        if me.status_code != 200:
            raise ToolError(f"GitHub rejected the token ({me.status_code})")
        owner = me.json()["login"]
        try:
            repo = clean_repo(repo, owner)
        except PagesError as e:
            raise ToolError(str(e))
        if repo.split("/")[0].lower() != owner.lower():
            raise ToolError(f"you can only publish to your own account ({owner}), not {repo.split('/')[0]}")
        r = await c.get(f"{API}/repos/{repo}")
        created = False
        if r.status_code == 404:
            r = await c.post(f"{API}/user/repos", json={"name": repo.split("/")[1], "private": False, "auto_init": False,
                                                        "description": "Published from Pocket Agent"})
            if r.status_code not in (200, 201):
                raise ToolError(f"could not create {repo}: {r.status_code} {r.text[:200]}")
            created = True
        elif r.status_code != 200:
            raise ToolError(f"GitHub said {r.status_code} for {repo}")
        elif r.json().get("private"):
            raise ToolError(f"{repo} is private; GitHub Pages on private repos needs a paid plan. Use a public repo.")
        tmp = Path(tempfile.mkdtemp(prefix="pages_"))
        try:
            for dp, dn, fn in os.walk(folder):
                dn[:] = [d for d in dn if d not in SKIP]
                for f in fn:
                    src = Path(dp) / f
                    dst = tmp / src.relative_to(folder)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            (tmp / ".nojekyll").write_text("")
            for cmd in (["git", "init", "-q", "-b", "gh-pages"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "Publish from Pocket Agent"]):
                rc, out = await workspace.run(cmd, cwd=tmp)
                if rc:
                    raise ToolError(f"git failed: {out[-300:]}")
            rc, out = await workspace.run(["git", "push", "-q", "--force", f"{GIT_BASE}/{repo}.git", "gh-pages:gh-pages"], cwd=tmp, auth=True, timeout=240)
            if rc:
                raise ToolError(f"push failed: {out[-300:]}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        pg = await c.post(f"{API}/repos/{repo}/pages", json={"source": {"branch": "gh-pages", "path": "/"}})
        note = ""
        if pg.status_code not in (200, 201, 409):
            note = f" (pushed, but turning Pages on said {pg.status_code}: enable it under the repo's Settings → Pages, branch gh-pages)"
    return f"Published {folder.name or '.'} to {repo} (branch gh-pages{', repo created' if created else ''}). Live at {url_for(repo)} in about a minute{note}."
