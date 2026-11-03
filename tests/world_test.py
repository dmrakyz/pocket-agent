"""World engine end to end: create, tick, memory, interjection, pinned model with no silent fallback, auto-pause."""
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
DATA = "/tmp/agent-world"
shutil.rmtree(DATA, ignore_errors=True)
APP_PORT, LLM_PORT = 9160, 9161
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
    c = httpx.Client(base_url=B, timeout=120)
    tok = c.post("/api/login", json={"password": "pw123"}).json()["token"]; A = {"Authorization": "Bearer " + tok}
    seen = lambda: httpx.get(f"http://127.0.0.1:{LLM_PORT}/seen").json()["models"]
    check("needs login", c.get("/api/worlds").status_code == 401)
    check("a world needs a cast", c.post("/api/worlds", headers=A, json={"name": "x", "cast": ""}).status_code == 400)
    w = c.post("/api/worlds", headers=A, json={"name": "Harbor", "premise": "A foggy harbor town.", "minutes": 1,
                                                "cast": "Mara: a stubborn ferry captain | wants to keep her route\nOtto: a nervous clerk | wants to leave town"}).json()
    wid = w["id"]
    check("create pins the model and clamps the interval", w["model"] and w["minutes"] == 10 and len(w["cast"]) == 2 and w["cast"][0]["goal"] == "wants to keep her route", str(w)[:300])
    r = c.post(f"/api/worlds/{wid}/tick", headers=A).json()
    check("a tick adds events to the log", r["ok"] and len(r["events"]) == 2 and len(r["world"]["log"]) == 3, str(r)[:300])
    check("characters remember", r["world"]["cast"][0]["memory"] and "remembered tick" in r["world"]["cast"][0]["memory"][0], str(r["world"]["cast"]))
    check("the second tick sees the first (log grows in the prompt)", "log=3" in c.post(f"/api/worlds/{wid}/tick", headers=A).json()["events"][0]["text"])
    c.post(f"/api/worlds/{wid}/say", headers=A, json={"text": "STRANGER knocks on the harbor office door"})
    check("interjection waits in the inbox", c.get(f"/api/worlds/{wid}", headers=A).json()["pending"] == 1)
    r = c.post(f"/api/worlds/{wid}/tick", headers=A).json()
    log = r["world"]["log"]
    check("interjection reaches the characters and lands in the log", "ZAP-INTERJECT" in r["events"][0]["text"] and any(e["who"] == "You" and "STRANGER" in e["text"] for e in log) and r["world"]["pending"] == 0, str(r)[:300])
    # editing the cast keeps memories
    c.patch(f"/api/worlds/{wid}", headers=A, json={"cast": "Mara: a stubborn ferry captain | wants peace\nIvy: a stowaway | wants breakfast"})
    w2 = c.get(f"/api/worlds/{wid}", headers=A).json()
    check("editing the cast keeps memories of those who stay", [x["name"] for x in w2["cast"]] == ["Mara", "Ivy"] and w2["cast"][0]["memory"] and not w2["cast"][1]["memory"], str(w2["cast"]))
    # pinned model: broken model fails, nothing is swapped
    c.patch(f"/api/worlds/{wid}", headers=A, json={"model": "mock:boom"})
    before = len(seen())
    r = c.post(f"/api/worlds/{wid}/tick", headers=A).json()
    check("a failing pinned model fails the tick", r["ok"] is False and r["world"]["fails"] == 1 and r["world"]["error"], str(r)[:300])
    used = seen()[before:]
    check("and no other model was tried in its place", set(used) == {"boom"}, str(used))
    for _ in range(2): r = c.post(f"/api/worlds/{wid}/tick", headers=A).json()
    check("three failures in a row pause the world", r["world"]["enabled"] is False and "paused" in r["world"]["error"], str(r["world"]["error"]))
    c.patch(f"/api/worlds/{wid}", headers=A, json={"model": w["model"], "enabled": True})
    r = c.post(f"/api/worlds/{wid}/tick", headers=A).json()
    check("fixing the model and resuming clears the error", r["ok"] and r["world"]["fails"] == 0 and r["world"]["error"] == "", str(r)[:200])
    lst = c.get("/api/worlds", headers=A).json()["worlds"]
    check("list is light (3 log lines) and the full world has the whole log", len(lst[0]["log"]) <= 3 and len(c.get(f"/api/worlds/{wid}", headers=A).json()["log"]) > 3)
    # ---- worlds with tools
    wt = c.post("/api/worlds", headers=A, json={"name": "Looky", "premise": "LOOKUP the fog", "tools": True, "cast": "Mara: captain | route\nOtto: clerk | leave"}).json()
    check("tools flag is stored, off by default elsewhere", wt["tools"] is True and w["tools"] is False)
    r = c.post(f"/api/worlds/{wt['id']}/tick", headers=A).json()
    check("a world with tools looks something up, then writes the beat from it", r["ok"] and "looked=1" in r["events"][0]["text"], str(r)[:300])
    lg = r["world"]["log"]
    check("the lookup is visible in the log", any(e["kind"] == "look" and "foggy harbor towns" in e["text"] for e in lg), str(lg[-4:]))
    wl = c.post("/api/worlds", headers=A, json={"name": "Loop", "premise": "LOOPFOREVER", "tools": True, "cast": "Mara: captain | route"}).json()
    r = c.post(f"/api/worlds/{wl['id']}/tick", headers=A).json()
    check("a model that never stops searching is cut off at 3 lookups and still produces a beat", r["ok"] and sum(1 for e in r["world"]["log"] if e["kind"] == "look") == 3 and "looked=3" in r["events"][0]["text"], str(r)[:400])
    r = c.post(f"/api/worlds/{wid}/tick", headers=A).json()
    check("a world with tools off never gets the lookup tools", r["ok"] and "looked=0" in r["events"][0]["text"] and not any(e["kind"] == "look" for e in r["world"]["log"]), str(r)[:300])
    c.delete(f"/api/worlds/{wt['id']}", headers=A); c.delete(f"/api/worlds/{wl['id']}", headers=A)
    c.delete(f"/api/worlds/{wid}", headers=A)
    check("delete", c.get("/api/worlds", headers=A).json()["worlds"] == [] and c.get(f"/api/worlds/{wid}", headers=A).status_code == 404)
finally:
    for p in procs: p.terminate()

print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
