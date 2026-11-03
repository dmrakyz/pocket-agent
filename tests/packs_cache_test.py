"""Packs survive a Render restart through a GitHub release: save after install, restore with one download, fall back to a build."""
import asyncio, os, shutil, subprocess, sys, time
import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = "/tmp/agent-packcache"
shutil.rmtree(DATA, ignore_errors=True)
os.environ.update({"DATA_DIR": DATA, "APP_PASSWORD": "x", "GITHUB_TOKEN": "ghp_testtoken", "STATE_REPO": "me/state",
                   "PACKS_GH_API": "http://127.0.0.1:9160", "PACKS_GH_UPLOADS": "http://127.0.0.1:9160"})
sys.path.insert(0, os.path.join(ROOT, "backend"))
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "fake_github:app", "--port", "9160", "--log-level", "warning"], cwd=os.path.join(ROOT, "tests"))
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


async def main():
    for _ in range(60):
        try:
            if httpx.get("http://127.0.0.1:9160/_state", timeout=1).status_code == 200: break
        except Exception: time.sleep(0.2)
    from app import packs
    packs.PACKS["tiny"] = {"title": "Tiny", "desc": "test pack", "needs_mb": 5, "kind": "pip", "pip": ["six"]}
    msg = await packs.enable("tiny", wait=120)
    check("first install builds the pack", packs.installed("tiny") and "installed" in msg, msg)
    for _ in range(100):
        if packs._cache_note.get("tiny", "").startswith(("saved", "GitHub save failed")): break
        await asyncio.sleep(0.2)
    st = httpx.get("http://127.0.0.1:9160/_state").json()
    check("the pack was saved as a release asset", any(a["name"].startswith("tiny-py") for a in st["assets"]) and packs._cache_note["tiny"].startswith("saved on GitHub"), str(packs._cache_note) + str(st))
    check("the GitHub token was sent as a bearer header", st["auth"] == "Bearer ghp_testtoken")
    marker = os.path.join(DATA, "packs", "tiny", "six.py")
    check("six was really installed into the pack", os.path.exists(marker))
    # simulate a Render restart: the disk is gone, memory state is gone
    shutil.rmtree(os.path.join(DATA, "packs"))
    packs._cache_note.clear(); packs._tasks.clear()
    async def boom(*a, **k): raise AssertionError("rebuilt instead of restoring from GitHub")
    real = packs._install_pip; packs._install_pip = boom
    try:
        msg = await packs.enable("tiny", wait=120)
    finally:
        packs._install_pip = real
    check("after a restart the pack comes back from GitHub without pip", packs.installed("tiny") and os.path.exists(marker) and packs._cache_note.get("tiny") == "restored from GitHub", str(packs._cache_note))
    st = [p for p in packs.status() if p["name"] == "tiny"][0]
    check("status tells the UI where it came from", st["cache"] == "restored from GitHub" and st["installed"])
    # a broken cache falls back to a normal build
    packs.remove("tiny")
    httpx.post("http://127.0.0.1:9160/repos/me/state/releases", json={})
    import app.packs as P
    old = P.GH; P.GH = "http://127.0.0.1:1"      # unreachable
    try:
        msg = await packs.enable("tiny", wait=120)
    finally:
        P.GH = old
    check("an unreachable GitHub just means a normal build", packs.installed("tiny") and os.path.exists(marker), msg)
    # no token: cache is off, nothing is attempted
    from app import config
    tok = config.GITHUB_TOKEN; config.GITHUB_TOKEN = ""
    try:
        check("no token, no cache", not packs._cache_on())
    finally:
        config.GITHUB_TOKEN = tok
try:
    asyncio.run(main())
finally:
    srv.terminate()
print("ALL PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
