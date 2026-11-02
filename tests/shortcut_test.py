"""Back-tap assistant end to end: real backend + mock model. Token auth, screenshot in, answer out, approvals refused, revocation."""
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import time

import httpx
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = "/tmp/agent-sc"
shutil.rmtree(DATA, ignore_errors=True)
APP_PORT, LLM_PORT = 9130, 9131
env = os.environ | {"APP_PASSWORD": "pw123", "DATA_DIR": DATA, "ALLOWED_ORIGINS": "*",
                    "PROVIDERS_JSON": json.dumps({"mock": {"base_url": f"http://127.0.0.1:{LLM_PORT}/v1", "api_key": "k"}})}
procs = [
    subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_llm:app", "--port", str(LLM_PORT), "--log-level", "warning"], cwd=os.path.join(ROOT, "tests"), env=env),
    subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(APP_PORT), "--log-level", "warning"], cwd=os.path.join(ROOT, "backend"), env=env),
]
B = f"http://127.0.0.1:{APP_PORT}"
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


def sess_of(chat):
    return httpx.get(B + f"/api/sessions/{chat}", headers=A, timeout=30).json()["session"]


def png(w=2400, h=1200):
    b = io.BytesIO(); Image.new("RGB", (w, h), "navy").save(b, "PNG"); return b.getvalue()


try:
    for _ in range(80):
        try:
            if httpx.get(B + "/health", timeout=1).status_code == 200: break
        except Exception: time.sleep(0.25)
    c = httpx.Client(base_url=B, timeout=90)
    tok = c.post("/api/login", json={"password": "pw123"}).json()["token"]; A = {"Authorization": "Bearer " + tok}
    check("no token yet: shortcut endpoint refuses", c.post("/api/shortcut", content=b"x").status_code == 401)
    check("status says not set up", c.get("/api/shortcut/status", headers=A).json()["enabled"] is False)
    check("making a token needs the app login", c.post("/api/shortcut/token", json={}).status_code == 401)
    t1 = c.post("/api/shortcut/token", headers=A, json={}).json()["token"]
    st = c.get("/api/shortcut/status", headers=A).json()
    check("token is shown once and only a hint is kept", t1.startswith("pa_") and st["enabled"] and t1 not in json.dumps(st) and st["hint"].startswith(t1[:6]), str(st))
    check("the app login token is not accepted here", c.post("/api/shortcut", headers=A, json={"text": "hi"}).status_code == 401)
    S1 = {"Authorization": "Bearer " + t1}
    r = c.post("/api/shortcut", headers=S1, json={"text": "hello from shortcut"})
    j = r.json()
    m0 = json.dumps(sess_of(j["chat"])["messages"][0]) if r.status_code == 200 else ""
    check("text works and the answer comes back", r.status_code == 200 and j["status"] == "done" and "You said" in j["answer"] and "hello from shortcut" in m0, r.text[:300])
    check("the style note told the model to be brief", "under 120 words" in m0)
    r = c.post("/api/shortcut", headers={**S1, "content-type": "image/png"}, content=png())
    j = r.json(); blob = json.dumps(sess_of(j["chat"])["messages"][0])
    check("a raw screenshot is accepted and answered", r.status_code == 200 and j["status"] == "done", r.text[:300])
    check("the screenshot was shrunk to a JPEG data URL", "data:image/jpeg;base64," in blob)
    im = Image.open(io.BytesIO(base64.b64decode(blob.split("data:image/jpeg;base64,")[1].split('"')[0].replace("\\", ""))))
    check("and fits within 1600px", max(im.size) <= 1600, str(im.size))
    r = c.post("/api/shortcut", headers=S1, json={"image_b64": base64.b64encode(png(300, 200)).decode(), "text": "what is this"})
    check("JSON with base64 image works", r.status_code == 200 and r.json()["status"] == "done", r.text[:200])
    r = c.post("/api/shortcut?text=quick%20question&url=https://example.com", headers=S1, content=b"")
    m1 = json.dumps(sess_of(r.json()["chat"])["messages"][0]) if r.status_code == 200 else ""
    check("query-string text and a link work", r.status_code == 200 and "quick question" in m1 and "example.com" in m1 and "untrusted" in m1, r.text[:200])
    check("an empty request is told what to send", "Nothing to work on" in c.post("/api/shortcut", headers=S1, content=b"").json()["answer"])
    check("garbage that isn't an image is a clean 400", c.post("/api/shortcut", headers={**S1, "content-type": "image/png"}, content=b"not an image").status_code == 400)
    r = c.post("/api/shortcut", headers=S1, json={"text": "EMBED please"})
    j = r.json()
    check("an approval is refused instead of hanging the Shortcut", r.status_code == 200 and j["status"] in ("done", "error") and j["answer"], r.text[:300])
    check("each use made a chat the user can read later", len([s for s in c.get("/api/sessions", headers=A).json() if "Back-tap" in s["title"]]) >= 5, str(c.get("/api/sessions", headers=A).json())[:200])
    t2 = c.post("/api/shortcut/token", headers=A, json={}).json()["token"]
    check("a new token kills the old one", c.post("/api/shortcut", headers=S1, json={"text": "hi"}).status_code == 401 and c.post("/api/shortcut", headers={"Authorization": "Bearer " + t2}, json={"text": "hi"}).status_code == 200)
    check("token also works as ?t= for tools that can't set headers", c.post("/api/shortcut?t=" + t2, json={"text": "hi"}).status_code == 200)
    c.delete("/api/shortcut", headers=A)
    check("revoking cuts it off", c.post("/api/shortcut", headers={"Authorization": "Bearer " + t2}, json={"text": "hi"}).status_code == 401 and c.get("/api/shortcut/status", headers=A).json()["enabled"] is False)
finally:
    for p in procs: p.terminate()

print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
