"""App drawer end to end: the agent builds an app, it is served sandboxed with the SDK, ask/data bridge endpoints, tokens, deletion."""
import json
import os
import shutil
import subprocess
import sys
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = "/tmp/agent-apps"
shutil.rmtree(DATA, ignore_errors=True)
APP_PORT, LLM_PORT = 9140, 9141
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


try:
    for _ in range(80):
        try:
            if httpx.get(B + "/health", timeout=1).status_code == 200: break
        except Exception: time.sleep(0.25)
    c = httpx.Client(base_url=B, timeout=60)
    tok = c.post("/api/login", json={"password": "pw123"}).json()["token"]; A = {"Authorization": "Bearer " + tok}
    check("apps list needs login", c.get("/api/apps").status_code == 401)
    check("empty at first", c.get("/api/apps", headers=A).json()["apps"] == [])
    sid = c.post("/api/sessions", headers=A, json={"mode": "yolo"}).json()["id"]
    r = c.post(f"/api/sessions/{sid}/messages", headers=A, json={"text": "MAKEAPP please"}); assert r.status_code == 200, r.text
    for _ in range(100):
        st = c.get(f"/api/sessions/{sid}", headers=A).json()
        if not st["run"].get("running") and any(m.get("role") == "assistant" and m.get("content") == "app ready" for m in st["session"]["messages"]): break
        time.sleep(0.3)
    msgs = st["session"]["messages"]
    check("the create_app result carries the app id for the chat card", any(m.get("_app") == "tip-calc" for m in msgs), str([m.get("_name") for m in msgs]))
    lst = c.get("/api/apps", headers=A).json()["apps"]
    check("the agent's app is listed once (second call updated it)", len(lst) == 1 and lst[0]["id"] == "tip-calc" and lst[0]["icon"] == "🧮" and lst[0]["ai"] is True, str(lst))
    u = c.post("/api/apps/tip-calc/token", headers=A).json()["url"]
    r = c.get(u)
    csp = r.headers.get("content-security-policy", "")
    check("served page has the update and the SDK", r.status_code == 200 and "Tip calc v2" in r.text and "window.agent=" in r.text, r.text[:200])
    check("CSP sandboxes it and blocks all network", "sandbox allow-scripts" in csp and "connect-src 'none'" in csp and "allow-same-origin" not in csp, csp)
    check("SDK is injected inside <head>", r.text.index("window.agent=") < r.text.index("<title>"))
    check("a forged token is refused", c.get("/app-host/tip-calc/9999999999.deadbeef").status_code == 403)
    other = c.post("/api/apps/tip-calc/token", headers=A).json()["url"]
    check("a token for one app does not open another", c.get(other.replace("tip-calc", "nope")).status_code in (403, 404))
    check("token for a missing app is a 404", c.post("/api/apps/nope/token", headers=A).status_code == 404)
    r = c.post("/api/apps/tip-calc/ask", headers=A, json={"prompt": "split 100 four ways", "system": "reply tersely"})
    check("agent.ask comes back with the model's answer", r.status_code == 200 and "You said" in r.json()["text"], r.text[:200])
    check("an empty prompt is refused", c.post("/api/apps/tip-calc/ask", headers=A, json={"prompt": "  "}).status_code == 400)
    check("ask needs login", c.post("/api/apps/tip-calc/ask", json={"prompt": "x"}).status_code == 401)
    check("data starts empty", c.get("/api/apps/tip-calc/data", headers=A).json()["data"] == {})
    check("set saves a value", c.put("/api/apps/tip-calc/data/n", headers=A, json={"value": {"a": [1, 2]}}).status_code == 200)
    check("and it reads back", c.get("/api/apps/tip-calc/data", headers=A).json()["data"] == {"n": {"a": [1, 2]}})
    check("an oversized value is refused", c.put("/api/apps/tip-calc/data/big", headers=A, json={"value": "x" * 120000}).status_code == 400)
    c.delete("/api/apps/tip-calc/data/n", headers=A)
    check("delete removes the key", c.get("/api/apps/tip-calc/data", headers=A).json()["data"] == {})
    r = c.patch("/api/apps/tip-calc", headers=A, json={"name": "Tip Master", "icon": "💸"})
    check("rename works", r.status_code == 200 and r.json()["name"] == "Tip Master" and r.json()["icon"] == "💸")
    codes = [c.post("/api/apps/tip-calc/ask", headers=A, json={"prompt": f"q{i}"}).status_code for i in range(45)]
    check("AI calls are rate-limited per app", codes.count(429) >= 1 and codes[0] == 200, str(codes))
    check("delete removes the app, its page and its data", c.delete("/api/apps/tip-calc", headers=A).status_code == 200 and c.get("/api/apps", headers=A).json()["apps"] == [] and c.get(u).status_code == 404)
    check("deleting twice is a 404", c.delete("/api/apps/tip-calc", headers=A).status_code == 404)
finally:
    for p in procs: p.terminate()

print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
