"""Dev-server preview (serve + /live proxy) and Publish to GitHub Pages, against a real Flask server and a local 'GitHub'."""
import asyncio, json, os, shutil, subprocess, sys, tempfile, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
shutil.rmtree("/tmp/agent-sp", ignore_errors=True)
os.environ.update({"APP_PASSWORD": "pw", "DATA_DIR": "/tmp/agent-sp", "GITHUB_TOKEN": "tok123"})
GITBASE = tempfile.mkdtemp(prefix="fakegh_")
os.environ["PAGES_GIT_BASE"] = "file://" + GITBASE
from fastapi import FastAPI, Request
import uvicorn
fake = FastAPI()
REPOS = {}


@fake.get("/user")
def user(): return {"login": "dmrakyz"}


@fake.get("/repos/{o}/{r}")
def getrepo(o: str, r: str):
    from fastapi import HTTPException
    if f"{o}/{r}" not in REPOS: raise HTTPException(404, "nf")
    return {"private": REPOS[f"{o}/{r}"]}


@fake.post("/user/repos")
def mk(body: dict):
    REPOS["dmrakyz/" + body["name"]] = body["private"]
    os.makedirs(f"{GITBASE}/dmrakyz", exist_ok=True)
    subprocess.run(["git", "init", "-q", "--bare", f"{GITBASE}/dmrakyz/{body['name']}.git"], check=True)
    return {"full_name": "dmrakyz/" + body["name"]}


PAGES = []


@fake.post("/repos/{o}/{r}/pages")
def pages(o: str, r: str, body: dict):
    from fastapi import HTTPException
    if f"{o}/{r}" in PAGES: raise HTTPException(409, "exists")
    PAGES.append(f"{o}/{r}"); return {}


threading.Thread(target=lambda: uvicorn.run(fake, port=9170, log_level="warning"), daemon=True).start()
os.environ["PAGES_GH_API"] = "http://127.0.0.1:9170"
import time; time.sleep(1.5)
from pathlib import Path
from fastapi.testclient import TestClient
from app import tools as T, perms
from app.main import app

fails = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: fails.append(n)

root = Path(tempfile.mkdtemp(prefix="ws_"))
sid = "sess1"
ctx = T.Ctx({"id": sid, "mode": "ask"}, root, None)
(root / "app.py").write_text('''import os
from flask import Flask, jsonify, redirect, request
a = Flask(__name__)
@a.get("/")
def i(): return "<h1>hello live</h1><script>fetch('api/n').then(r=>r.json()).then(j=>document.title=j.n)</script>"
@a.get("/api/n")
def n(): return jsonify(n=41+1)
@a.post("/api/echo")
def e(): return jsonify(got=request.get_json())
@a.get("/go")
def go(): return redirect("/api/n")
a.run(host="127.0.0.1", port=int(os.environ["PORT"]))
''')
(root / "bad.py").write_text("raise SystemExit('boom: cannot start')\n")

async def main():
    r = await T.t_serve(ctx, {"command": "python3 app.py"})
    check("serve returns once the port answers, with a live preview card", r["preview"] == "live:" and sid in T.SERVERS, str(r))
    c = TestClient(app)
    from app import main as M
    import time as _t
    exp = str(int(_t.time()) + 600); ptok = f"{exp}.{M._preview_sig(sid, exp)}"
    base = f"/live/{sid}/{ptok}/"
    g = c.get(base)
    check("proxy serves the dev server page", g.status_code == 200 and "hello live" in g.text, g.text[:100])
    check("page is sandboxed (CSP) like static previews", "sandbox" in g.headers.get("content-security-policy", ""))
    check("relative fetch path works", c.get(base + "api/n").json() == {"n": 42})
    check("POST bodies are forwarded", c.post(base + "api/echo", json={"x": 1}).json() == {"got": {"x": 1}})
    rd = c.get(base + "go", follow_redirects=False)
    check("absolute redirects are rewritten under the prefix", rd.status_code in (301, 302, 308) and rd.headers["location"] == base[:-1] + "/api/n", rd.headers.get("location"))
    check("bad token refused", c.get(f"/live/{sid}/{exp}.deadbeef/").status_code == 403)
    check("other chat can't use this chat's token", c.get(f"/live/other/{ptok}/").status_code == 403)
    exp2 = str(int(_t.time()) + 600)
    check("a chat without a server gets 404", c.get(f"/live/other/{exp2}.{M._preview_sig('other', exp2)}/").status_code == 404)
    # restart replaces
    old = T.SERVERS[sid]["port"]
    await T.t_serve(ctx, {"command": "python3 app.py"})
    check("starting again replaces the server (new port, old one dead)", T.SERVERS[sid]["port"] != old and not T._port_open(old))
    check("env secrets are not visible to the server", "tok123" not in open(T.JOBS[T.SERVERS[sid]["jid"]]["log"], "rb").read().decode())
    check("serve_stop stops it", await T.t_serve_stop(ctx, {}) == "stopped" and sid not in T.SERVERS)
    await asyncio.sleep(0.3)
    check("port is closed after stop", not T._port_open(T.SERVERS.get(sid, {"port": old})["port"]))
    try:
        await T.t_serve(ctx, {"command": "python3 bad.py"})
        check("a server that dies reports its log", False)
    except T.ToolError as e:
        check("a server that dies reports its log", "boom" in str(e), str(e))
    check("deny-list applies to serve too", perms.decide({"mode": "yolo", "allow": []}, "serve", {"command": "cat /proc/self/environ"})[0] == "deny")
    check("serve asks in ask mode, denied in plan", perms.decide({"mode": "ask", "allow": []}, "serve", {"command": "x"})[0] == "ask" and perms.decide({"mode": "plan", "allow": []}, "serve", {"command": "x"})[0] == "deny")

    # ---- publish
    site = root / "site"; (site / "css").mkdir(parents=True)
    (site / "index.html").write_text("<h1>my site</h1>"); (site / "css/a.css").write_text("b{}")
    (site / "node_modules").mkdir(); (site / "node_modules/x.js").write_text("no")
    out = await T.t_publish_pages(ctx, {"folder": "site", "repo": "my-site"})
    check("publish creates the repo, pushes gh-pages, returns the Pages URL", "https://dmrakyz.github.io/my-site/" in out and "dmrakyz/my-site" in REPOS and "repo created" in out, out)
    chk = tempfile.mkdtemp()
    subprocess.run(["git", "clone", "-q", "-b", "gh-pages", f"{GITBASE}/dmrakyz/my-site.git", chk], check=True)
    check("branch holds index.html, nested files and .nojekyll but not node_modules", (Path(chk) / "index.html").exists() and (Path(chk) / "css/a.css").exists() and (Path(chk) / ".nojekyll").exists() and not (Path(chk) / "node_modules").exists())
    (site / "index.html").write_text("<h1>v2</h1>")
    out2 = await T.t_publish_pages(ctx, {"folder": "site", "repo": "dmrakyz/my-site"})
    check("republishing works (force push, pages already on is fine)", "repo created" not in out2 and "turning Pages on said" not in out2, out2)
    for repo, want in (("someoneelse/x", "your own account"), ("bad name!", "owner/name")):
        try:
            await T.t_publish_pages(ctx, {"folder": "site", "repo": repo}); check("refuses " + repo, False)
        except T.ToolError as e:
            check("refuses " + repo, want in str(e), str(e))
    REPOS["dmrakyz/priv"] = True
    try:
        await T.t_publish_pages(ctx, {"folder": "site", "repo": "priv"}); check("private repo refused", False)
    except T.ToolError as e:
        check("private repo refused", "private" in str(e))
    try:
        await T.t_publish_pages(ctx, {"folder": "css", "repo": "my-site"}); check("folder without index.html refused", False)
    except T.ToolError as e:
        check("folder without index.html refused", "index.html" in str(e) or "No such" in str(e) or "not exist" in str(e).lower(), str(e))
    check("publish always asks, even in yolo, denied in plan", perms.decide({"mode": "yolo", "allow": []}, "publish_pages", {})[0] == "ask" and perms.decide({"mode": "plan", "allow": []}, "publish_pages", {})[0] == "deny")
    check("publish never gets 'always allow'", perms.always_rule("publish_pages", {}) is None)
asyncio.run(main())
for sid_ in list(T.SERVERS): T._stop_server(sid_)
print("\nALL PASSED" if not fails else "\nFAILED: " + str(fails))
sys.exit(1 if fails else 0)
