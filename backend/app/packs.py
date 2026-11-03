"""Toolchain packs: heavy toolchains the agent switches on only when a task needs them.

Nothing here is baked into the Docker image, so the image stays small and the Render build stays fast.
Installed packs live in DATA_DIR/packs (ephemeral on Render: after a restart they are simply "not installed"
and the agent re-enables them on demand). Idle RAM is unaffected either way; a pack only costs disk, and RAM
only while a program that uses it is running (bash runs have their own memory guard).
"""
import asyncio
import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

import httpx

from . import config

ROOT = Path(config.DATA_DIR) / "packs"

PACKS: dict[str, dict] = {
    "py-science": {
        "title": "Python science stack",
        "desc": "scipy, pandas, sympy, scikit-learn, networkx (numpy and matplotlib are always there)",
        "needs_mb": 450, "kind": "pip",
        "pip": ["scipy", "pandas", "sympy", "scikit-learn", "networkx"],
    },
    "cpp": {
        "title": "C/C++ toolchain",
        "desc": "cmake, ninja and clang/clang++ (via zig). g++ and make are always there",
        "needs_mb": 600, "kind": "pip",
        "pip": ["cmake", "ninja", "ziglang"],
    },
    "emscripten": {
        "title": "Emscripten (C/C++ to WebAssembly)",
        "desc": "emcc/em++ to compile C or C++ into .wasm + .js that runs in a preview page",
        "needs_mb": 2200, "kind": "emsdk",
    },
}

_tasks: dict[str, asyncio.Task] = {}
_last_error: dict[str, str] = {}
_cache_note: dict[str, str] = {}     # what the GitHub cache did for each pack, shown in Settings

# GitHub Releases on the state repo keep a copy of each installed pack (an asset may be up to 2 GB), so a Render restart
# restores it with one download instead of rebuilding. Everything here is best effort: no token or repo means no cache.
GH = os.environ.get("PACKS_GH_API", "https://api.github.com")
GH_UP = os.environ.get("PACKS_GH_UPLOADS", "https://uploads.github.com")
CACHE_TAG = "pack-cache"
MAX_ASSET = 1_900_000_000


def _dir(name: str) -> Path:
    return ROOT / name


def installed(name: str) -> bool:
    return (_dir(name) / ".installed").exists()


def _size_mb(p: Path) -> int:
    total = 0
    for dp, _, fs in os.walk(p):
        for f in fs:
            try:
                total += os.lstat(os.path.join(dp, f)).st_size
            except OSError:
                pass
    return total // (1024 * 1024)


def free_mb() -> int:
    d = Path(config.DATA_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(d).free // (1024 * 1024)


def status() -> list[dict]:
    out = []
    for name, p in PACKS.items():
        on = installed(name)
        out.append({"name": name, "title": p["title"], "desc": p["desc"], "installed": on,
                    "installing": name in _tasks and not _tasks[name].done(),
                    "size_mb": _size_mb(_dir(name)) if on else 0, "needs_mb": p["needs_mb"],
                    "error": _last_error.get(name, ""), "cache": _cache_note.get(name, "")})
    return out


# ------------------------------------------------------------------ environment wiring
def env_patch(env: dict) -> dict:
    """Add every installed pack to a subprocess environment (PATH / PYTHONPATH / emscripten settings)."""
    path, pypath = [], []
    if installed("py-science"):
        pypath.append(str(_dir("py-science")))
    if installed("cpp"):
        pypath.append(str(_dir("cpp")))
        path += [str(_dir("cpp") / "bin"), str(_dir("cpp") / "wrap")]
        env["ZIG_GLOBAL_CACHE_DIR"] = str(Path(env.get("HOME", "/tmp")) / ".zig-cache")
    if installed("emscripten"):
        e = _dir("emscripten") / "emsdk"
        up = e / "upstream" / "emscripten"
        nodes = sorted((e / "node").glob("*/bin"))
        path += [str(e), str(up)] + [str(n) for n in nodes]
        env["EMSDK"] = str(e)
        env["EM_CONFIG"] = str(e / ".emscripten")
        env["EM_CACHE"] = str(_dir("emscripten") / "cache")
        if nodes:
            env["EMSDK_NODE"] = str(nodes[0] / "node")
    if path:
        env["PATH"] = os.pathsep.join(path + [env.get("PATH", "")])
    if pypath:
        env["PYTHONPATH"] = os.pathsep.join(pypath + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env


# ------------------------------------------------------------------ install / remove
async def _sh(cmd: list[str], cwd: Path | None = None, timeout: int = 1500, env: dict | None = None) -> tuple[int, str]:
    e = dict(os.environ)
    for k in config.SECRET_ENV_KEYS:
        e.pop(k, None)
    e.update(env or {})
    e["HOME"] = str(Path(config.DATA_DIR) / "home")
    Path(e["HOME"]).mkdir(parents=True, exist_ok=True)
    p = await asyncio.create_subprocess_exec(*cmd, cwd=str(cwd) if cwd else None, env=e,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        p.kill()
        return 124, "timed out"
    return p.returncode, out.decode("utf-8", "replace")[-1500:]


async def _install_pip(name: str, d: Path) -> None:
    pkgs = PACKS[name]["pip"]
    rc, out = await _sh([sys.executable, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
                         "--only-binary=:all:", "--target", str(d), *pkgs])
    if rc:
        raise RuntimeError("pip failed: " + out[-600:])
    if name == "cpp":
        wrap = d / "wrap"
        wrap.mkdir(exist_ok=True)
        py = sys.executable
        for exe, sub in (("clang", "cc"), ("clang++", "c++"), ("zigcc", "cc"), ("zigc++", "c++")):
            f = wrap / exe
            f.write_text(f'#!/bin/sh\nexec "{py}" -m ziglang {sub} "$@"\n')
            f.chmod(0o755)


async def _install_emsdk(d: Path) -> None:
    if not shutil.which("git"):
        raise RuntimeError("git is missing")
    e = d / "emsdk"
    rc, out = await _sh(["git", "clone", "--depth", "1", "https://github.com/emscripten-core/emsdk.git", str(e)], timeout=300)
    if rc:
        raise RuntimeError("could not clone emsdk: " + out[-400:])
    rc, out = await _sh(["python3", "emsdk.py", "install", "latest"], cwd=e, timeout=1500)
    if rc:
        raise RuntimeError("emsdk install failed: " + out[-600:])
    rc, out = await _sh(["python3", "emsdk.py", "activate", "latest"], cwd=e, timeout=300)
    if rc:
        raise RuntimeError("emsdk activate failed: " + out[-400:])
    if not (e / ".emscripten").exists():
        raise RuntimeError("emsdk finished but wrote no .emscripten config")


def _cache_on() -> bool:
    return bool(config.GITHUB_TOKEN and config.STATE_REPO)


def _asset_name(name: str) -> str:
    return f"{name}-py{sys.version_info.major}{sys.version_info.minor}-{platform.machine()}.tar.gz"


def _hdr(extra: dict | None = None) -> dict:
    return {"Authorization": f"Bearer {config.GITHUB_TOKEN}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", **(extra or {})}


async def _cache_fetch(name: str) -> bool:
    """Restore a pack from the GitHub release, if one was saved. False on any problem (then the pack is built normally)."""
    if not _cache_on():
        return False
    tmp = ROOT / f"{name}.dl.tar.gz"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=30), follow_redirects=True) as c:
            r = await c.get(f"{GH}/repos/{config.STATE_REPO}/releases/tags/{CACHE_TAG}", headers=_hdr())
            if r.status_code != 200:
                return False
            asset = next((a for a in r.json().get("assets", []) if a["name"] == _asset_name(name)), None)
            if not asset:
                return False
            _cache_note[name] = "downloading from GitHub…"
            async with c.stream("GET", f"{GH}/repos/{config.STATE_REPO}/releases/assets/{asset['id']}",
                                headers=_hdr({"Accept": "application/octet-stream"})) as resp:
                if resp.status_code != 200:
                    return False
                with open(tmp, "wb") as f:
                    async for chunk in resp.aiter_bytes(1 << 20):
                        f.write(chunk)
        rc, _ = await _sh(["tar", "xzf", str(tmp), "-C", str(ROOT)], timeout=900)
        ok = rc == 0 and installed(name)
        _cache_note[name] = "restored from GitHub" if ok else ""
        return ok
    except Exception as e:  # noqa: BLE001
        _cache_note[name] = ""
        _last_error[name] = f"GitHub cache not usable ({type(e).__name__}); building instead"
        return False
    finally:
        tmp.unlink(missing_ok=True)


async def _cache_store(name: str) -> None:
    """Upload the freshly installed pack to the release (replacing an older copy). Runs in the background, never fails the install."""
    tmp = ROOT / f"{name}.up.tar.gz"
    try:
        _cache_note[name] = "saving to GitHub…"
        rc, out = await _sh(["nice", "-n", "10", "tar", "czf", str(tmp), "-C", str(ROOT), name], timeout=1800)
        if rc:
            raise RuntimeError("could not pack: " + out[-200:])
        size = tmp.stat().st_size
        if size > MAX_ASSET:
            raise RuntimeError(f"{size // (1024 * 1024)} MB is over GitHub's 2 GB asset limit")
        repo = config.STATE_REPO
        async with httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=30)) as c:
            r = await c.get(f"{GH}/repos/{repo}/releases/tags/{CACHE_TAG}", headers=_hdr())
            if r.status_code == 404:
                r = await c.post(f"{GH}/repos/{repo}/releases", headers=_hdr(), json={
                    "tag_name": CACHE_TAG, "name": "Pack cache", "prerelease": True,
                    "body": "Installed toolchain packs, saved by Pocket Agent so a restart restores them quickly. Safe to delete."})
            r.raise_for_status()
            rel = r.json()
            for a in rel.get("assets", []):
                if a["name"] == _asset_name(name):
                    await c.delete(f"{GH}/repos/{repo}/releases/assets/{a['id']}", headers=_hdr())

            async def body():
                with open(tmp, "rb") as f:
                    while chunk := f.read(1 << 20):
                        yield chunk
            up = await c.post(f"{GH_UP}/repos/{repo}/releases/{rel['id']}/assets", params={"name": _asset_name(name)},
                              headers=_hdr({"Content-Type": "application/gzip", "Content-Length": str(size)}), content=body())
            up.raise_for_status()
        _cache_note[name] = f"saved on GitHub ({size // (1024 * 1024)} MB)"
    except Exception as e:  # noqa: BLE001
        _cache_note[name] = f"GitHub save failed: {str(e)[:120]}"
    finally:
        tmp.unlink(missing_ok=True)


async def _do_install(name: str) -> None:
    d = _dir(name)
    try:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        ROOT.mkdir(parents=True, exist_ok=True)
        if await _cache_fetch(name):
            _last_error.pop(name, None)
            return
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        if PACKS[name]["kind"] == "pip":
            await _install_pip(name, d)
        else:
            await _install_emsdk(d)
        (d / ".installed").write_text(json.dumps({"at": time.time()}))
        _last_error.pop(name, None)
        if _cache_on():
            _tasks[name + ":save"] = asyncio.create_task(_cache_store(name))
    except Exception as e:  # noqa: BLE001
        _last_error[name] = str(e)[:700]
        shutil.rmtree(d, ignore_errors=True)
        raise


async def enable(name: str, wait: int = 90) -> str:
    """Install a pack (or join an install in progress). Waits up to `wait` seconds, then reports progress."""
    if name not in PACKS:
        raise ValueError(f"unknown pack {name!r}; available: {', '.join(PACKS)}")
    if installed(name):
        return f"Pack {name} is already installed and on PATH for bash."
    t = _tasks.get(name)
    if not t or t.done():
        need, free = PACKS[name]["needs_mb"], free_mb()
        if free < need:
            raise ValueError(f"not enough disk for {name}: needs about {need} MB, {free} MB free. Remove another pack first.")
        t = asyncio.create_task(_do_install(name))
        _tasks[name] = t
    try:
        await asyncio.wait_for(asyncio.shield(t), wait)
    except asyncio.TimeoutError:
        return f"Pack {name} is still installing (it can take several minutes). Call pack_enable again to check; do not start other work that needs it yet."
    exc = t.exception()
    if exc:
        raise ValueError(f"install of {name} failed: {_last_error.get(name) or exc}")
    return f"Pack {name} installed. {PACKS[name]['desc']}. It is on PATH/PYTHONPATH for every bash call from now on. {HINTS.get(name, '')}"


def remove(name: str) -> bool:
    if name not in PACKS:
        return False
    t = _tasks.get(name)
    if t and not t.done():
        t.cancel()
    shutil.rmtree(_dir(name), ignore_errors=True)
    _last_error.pop(name, None)
    _cache_note.pop(name, None)
    return True


HINTS = {
    "py-science": "Import scipy/pandas/sympy/sklearn/networkx as usual; memory is only used while the script runs.",
    "cpp": "Use cmake/ninja, or clang/clang++ (zig cc under the hood; also `zigcc`/`zigc++`). Plain g++ still works too.",
    "emscripten": "Compile with `emcc file.c -O2 -o out.html` or `em++ main.cpp -O2 -sEXPORTED_FUNCTIONS=... -o out.js`, "
                  "put the .js/.wasm next to an index.html in the workspace, then call preview. Keep builds small: the server has little RAM.",
}
