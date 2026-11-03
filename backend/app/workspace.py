"""Per-session git workspaces. Render's disk is ephemeral, so git (GitHub) is the source of truth."""
import asyncio
import base64
import os
import shutil
from pathlib import Path

from . import config

HOME = Path(config.DATA_DIR) / "home"
WS_ROOT = Path(config.DATA_DIR) / "ws"


def ws_path(session_id: str) -> Path:
    return WS_ROOT / session_id


def branch_for(session: dict) -> str:
    return f"agent/{session['id'][:8]}" if session.get("repo") else f"scratch/{session['id']}"


def remote_repo(session: dict) -> str:
    if session.get("_ephemeral"):
        return ""
    return session.get("repo") or config.STATE_REPO


def _auth_args() -> list[str]:
    if not config.GITHUB_TOKEN:
        return []
    b = base64.b64encode(f"x-access-token:{config.GITHUB_TOKEN}".encode()).decode()
    return ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {b}"]


def clean_env() -> dict:
    """Environment for subprocesses: no API keys, no tokens."""
    HOME.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in config.SECRET_ENV_KEYS}
    env.update({
        "HOME": str(HOME), "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": config.GIT_USER_NAME,
        "GIT_AUTHOR_EMAIL": config.GIT_USER_EMAIL, "GIT_COMMITTER_NAME": config.GIT_USER_NAME,
        "GIT_COMMITTER_EMAIL": config.GIT_USER_EMAIL, "PAGER": "cat", "TERM": "dumb",
    })
    from . import packs
    return packs.env_patch(env)


async def run(cmd: list[str], cwd: Path | None = None, auth: bool = False, timeout: int = 180):
    """Run a git (or other) command. Returns (returncode, combined output)."""
    if auth and cmd and cmd[0] == "git":
        cmd = ["git"] + _auth_args() + cmd[1:]
    p = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(cwd) if cwd else None, env=clean_env(),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        p.kill()
        return 124, "timed out"
    text = out.decode("utf-8", "replace")
    if config.GITHUB_TOKEN:
        text = text.replace(config.GITHUB_TOKEN, "***")
    return p.returncode, text


async def ensure_workspace(session: dict) -> Path:
    """Make sure the session's workspace exists locally (re-cloning from GitHub after a restart)."""
    path = ws_path(session["id"])
    if (path / ".git").exists():
        return path
    if not shutil.which("git"):
        raise RuntimeError("git is not installed on this server. Deploy with the Dockerfile (render.yaml does this).")
    WS_ROOT.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    branch = branch_for(session)
    repo = remote_repo(session)
    have_remote = bool(repo and config.GITHUB_TOKEN)
    if have_remote:
        url = f"https://github.com/{repo}.git"
        rc, _ = await run(["git", "ls-remote", "--exit-code", "--heads", url, branch], auth=True, timeout=60)
        if rc == 0:   # our branch already exists (restart / second device): continue from it
            rc, out = await run(["git", "clone", "--branch", branch, url, str(path)], auth=True, timeout=300)
            if rc == 0:
                await _post_clone(path)
                return path
        if session.get("repo"):   # user repo: clone default branch, then branch off it
            rc, out = await run(["git", "clone", url, str(path)], auth=True, timeout=300)
            if rc != 0:
                raise RuntimeError(f"git clone failed: {out[-400:]}")
            await run(["git", "checkout", "-B", branch], cwd=path)
            await _post_clone(path)
            return path
    # scratch workspace (or no GitHub configured): plain local repo
    path.mkdir(parents=True, exist_ok=True)
    await run(["git", "init", "-b", branch], cwd=path)
    if have_remote:
        await run(["git", "remote", "add", "origin", f"https://github.com/{repo}.git"], cwd=path)
    await _post_clone(path)
    return path


async def _post_clone(path: Path):
    await run(["git", "config", "user.name", config.GIT_USER_NAME], cwd=path)
    await run(["git", "config", "user.email", config.GIT_USER_EMAIL], cwd=path)


async def head(path: Path) -> str:
    rc, out = await run(["git", "rev-parse", "HEAD"], cwd=path)
    return out.strip() if rc == 0 else ""


async def commit_all(path: Path, message: str) -> str:
    """Snapshot everything. Returns the HEAD sha (existing one if nothing changed)."""
    await run(["git", "add", "-A"], cwd=path)
    if not await head(path):     # brand-new repo: make a root commit so there is always something to rewind to
        await run(["git", "commit", "-q", "-m", "workspace start", "--allow-empty"], cwd=path)
    rc, _ = await run(["git", "diff", "--cached", "--quiet"], cwd=path)
    if rc != 0:
        await run(["git", "commit", "-q", "-m", message[:200], "--allow-empty-message"], cwd=path)
    return await head(path)


async def push(session: dict, path: Path) -> str:
    if not (remote_repo(session) and config.GITHUB_TOKEN):
        return "no GitHub configured; workspace is local only"
    branch = branch_for(session)
    rc, out = await run(["git", "push", "-u", "origin", f"HEAD:refs/heads/{branch}"], cwd=path, auth=True, timeout=240)
    if rc != 0:   # we own agent/* and scratch/* branches, so overwrite after a rewind
        rc, out = await run(["git", "push", "--force", "-u", "origin", f"HEAD:refs/heads/{branch}"],
                            cwd=path, auth=True, timeout=240)
    return f"pushed to {remote_repo(session)}@{branch}" if rc == 0 else f"push failed: {out[-300:]}"


async def rewind(path: Path, sha: str) -> str:
    rc, out = await run(["git", "reset", "--hard", sha], cwd=path)
    await run(["git", "clean", "-fd"], cwd=path)
    return out if rc != 0 else "ok"


def drop_workspace(session_id: str):
    shutil.rmtree(ws_path(session_id), ignore_errors=True)
