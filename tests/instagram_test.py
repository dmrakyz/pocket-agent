"""Instagram DM bridge end to end: real backend + mock model + a fake Graph API that records every call."""
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import threading
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = "/tmp/agent-ig"
shutil.rmtree(DATA, ignore_errors=True)
CALLS: list = []
FAKE_PORT, APP_PORT, LLM_PORT = 9122, 9120, 9121
APPSECRET = "appsecret-test"

from fastapi import FastAPI, Request          # noqa: E402
from fastapi.responses import Response       # noqa: E402
import uvicorn                                 # noqa: E402

fake = FastAPI()
JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb004300") + b"\x00" * 64 + b"\xff\xd9"


@fake.get("/me")
async def g_me(request: Request):
    CALLS.append(("me", {"auth": request.headers.get("authorization")}))
    return {"username": "burner_test", "user_id": "111"}


@fake.post("/me/subscribed_apps")
async def g_sub(request: Request):
    CALLS.append(("subscribed_apps", dict(request.query_params)))
    return {"success": True}


@fake.post("/me/messages")
async def g_msg(request: Request):
    body = await request.json()
    CALLS.append(("messages", body))
    return {"recipient_id": body["recipient"]["id"], "message_id": "m"}


@fake.get("/img.jpg")
async def g_img():
    return Response(JPEG, media_type="image/jpeg")


threading.Thread(target=lambda: uvicorn.run(fake, port=FAKE_PORT, log_level="warning"), daemon=True).start()

env = os.environ | {
    "APP_PASSWORD": "pw123", "DATA_DIR": DATA, "ALLOWED_ORIGINS": "*",
    "PROVIDERS_JSON": json.dumps({"mock": {"base_url": f"http://127.0.0.1:{LLM_PORT}/v1", "api_key": "k"}}),
    "INSTAGRAM_ACCESS_TOKEN": "igtoken", "INSTAGRAM_APP_SECRET": APPSECRET, "INSTAGRAM_API_BASE": f"http://127.0.0.1:{FAKE_PORT}", "PUBLIC_URL": "https://example.test",
}
procs = [
    subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_llm:app", "--port", str(LLM_PORT), "--log-level", "warning"], cwd=os.path.join(ROOT, "tests"), env=env),
    subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(APP_PORT), "--log-level", "warning"], cwd=os.path.join(ROOT, "backend"), env=env),
]
B = f"http://127.0.0.1:{APP_PORT}"
fails = []
SECRET = hashlib.sha256(b"salt-v1:pw123").hexdigest()
mac = lambda label: hmac.new(SECRET.encode(), ("instagram-" + label).encode(), hashlib.sha256).hexdigest()[:32]
HOOK = f"/instagram/{mac('path')}"
VERIFY = mac("verify")[:20]


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


def texts(since=0, to=None):
    return [p["message"]["text"] for m, p in CALLS[since:] if m == "messages" and "message" in p and (to is None or p["recipient"]["id"] == to)]


def wait(pred, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.2)
    return False


mid = [0]
def deliver(c, sender, sig=True, **msg):
    mid[0] += 1
    body = json.dumps({"object": "instagram", "entry": [{"id": "111", "time": 1, "messaging": [{"sender": {"id": sender}, "recipient": {"id": "111"}, "timestamp": 1, "message": {"mid": f"mid{mid[0]}", **msg}}]}]}).encode()
    headers = {"content-type": "application/json"}
    if sig:
        headers["x-hub-signature-256"] = "sha256=" + hmac.new(APPSECRET.encode(), body, hashlib.sha256).hexdigest()
    return c.post(HOOK, headers=headers, content=body), body


try:
    for _ in range(80):
        try:
            if httpx.get(B + "/health", timeout=1).status_code == 200: break
        except Exception: time.sleep(0.25)
    c = httpx.Client(base_url=B, timeout=30)
    check("startup looked up the account with the bearer token", wait(lambda: any(m == "me" and p["auth"] == "Bearer igtoken" for m, p in CALLS)))
    r = c.get(HOOK, params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "98765"})
    check("Meta's verification handshake echoes the challenge", r.status_code == 200 and r.text == "98765", r.text)
    check("wrong verify token is refused", c.get(HOOK, params={"hub.mode": "subscribe", "hub.verify_token": "no", "hub.challenge": "1"}).status_code == 404)
    check("wrong path is refused", c.get("/instagram/deadbeef", params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1"}).status_code == 404)

    n = len(CALLS)
    check("unsigned POST is rejected", deliver(c, "777", sig=False, text="hi")[0].status_code == 404)
    r, body = deliver(c, "777", text="hi")
    r2 = c.post(HOOK, headers={"content-type": "application/json", "x-hub-signature-256": "sha256=" + "0" * 64}, content=body)
    check("bad signature is rejected", r2.status_code == 404)
    check("signed POST is accepted", r.status_code == 200)
    check("unpaired sender is told the account is private", wait(lambda: any("private" in t for t in texts(n, "777"))), str(texts(n)))
    n2 = len(CALLS); deliver(c, "777", text="hello?"); time.sleep(0.8)
    check("and is not spammed with the same reply again", not texts(n2, "777"))

    tok = c.post("/api/login", json={"password": "pw123"}).json()["token"]; A = {"Authorization": "Bearer " + tok}
    st = c.get("/api/instagram", headers=A, params={"base": "https://app.example"}).json()
    check("status shows the callback URL and verify token", st["enabled"] and st["callback"] == "https://app.example" + HOOK and st["verify_token"] == VERIFY and st["username"] == "burner_test", str(st))
    r = c.post("/api/instagram/connect", headers=A, json={"base": "https://app.example"}).json()
    check("connect subscribes to messages and returns a code", len(r.get("code", "")) == 6 and any(m == "subscribed_apps" and p.get("subscribed_fields") == "messages" for m, p in CALLS), str(r))
    n = len(CALLS); deliver(c, "777", text="pair WRONG1")
    check("wrong code refused", wait(lambda: any("wrong or expired" in t for t in texts(n, "777"))))
    n = len(CALLS); deliver(c, "777", text=f"pair {r['code']}")
    check("right code pairs the sender", wait(lambda: any("Paired" in t for t in texts(n, "777"))))
    n = len(CALLS); deliver(c, "888", text=f"pair {r['code']}")
    check("a code works once, and other senders stay out", wait(lambda: any("wrong or expired" in t for t in texts(n, "888"))))
    check("status shows one paired account", c.get("/api/instagram", headers=A).json()["paired"] == 1)

    n = len(CALLS); deliver(c, "777", text="hello there")
    check("paired sender gets the agent's answer", wait(lambda: any("You said: hello there" in t for t in texts(n, "777"))), str(texts(n)))
    check("typing indicator was shown", any(m == "messages" and p.get("sender_action") == "typing_on" for m, p in CALLS[n:]))
    n = len(CALLS); r, body = deliver(c, "777", text="hello there")
    c.post(HOOK, headers={"content-type": "application/json", "x-hub-signature-256": "sha256=" + hmac.new(APPSECRET.encode(), body, hashlib.sha256).hexdigest()}, content=body)
    time.sleep(1.5); check("a redelivered message id is processed once", sum("You said" in t for t in texts(n, "777")) == 1, str(texts(n)))
    uid = mid[0] + 1
    echo = json.dumps({"object": "instagram", "entry": [{"id": "111", "messaging": [{"sender": {"id": "111"}, "recipient": {"id": "777"}, "message": {"mid": "echo1", "is_echo": True, "text": "x"}}]}]}).encode()
    n = len(CALLS); c.post(HOOK, headers={"content-type": "application/json", "x-hub-signature-256": "sha256=" + hmac.new(APPSECRET.encode(), echo, hashlib.sha256).hexdigest()}, content=echo)
    time.sleep(0.8); check("echoes of our own messages are ignored", not texts(n))

    n = len(CALLS); deliver(c, "777", text="EMBED please")
    check("approval arrives as a YES/NO text", wait(lambda: any("Approve?" in t and "Reply YES or NO" in t for t in texts(n, "777"))), str(texts(n)))
    n2 = len(CALLS); deliver(c, "777", text="maybe"); check("anything else gets a nudge", wait(lambda: any("Reply YES" in t for t in texts(n2, "777"))))
    n3 = len(CALLS); deliver(c, "777", text="yes")
    check("YES lets the run finish", wait(lambda: any("Allowed" in t for t in texts(n3, "777"))) and wait(lambda: any("stay in the app" in t for t in texts(n3, "777")) or any("You said" in t or "done" in t.lower() for t in texts(n3, "777"))), str(texts(n3)))
    check("embed markup is stripped", not any("ws:" in t for t in texts(n, "777")), str(texts(n)))
    check("every message is under the byte limit and has no markdown bold", all(len(t.encode()) <= 1000 and "**" not in t for t in texts(0)))

    n = len(CALLS); deliver(c, "777", attachments=[{"type": "image", "payload": {"url": f"http://127.0.0.1:{FAKE_PORT}/img.jpg"}}], text="what is this")
    check("images are downloaded and answered", wait(lambda: any("You said" in t for t in texts(n, "777"))), str(texts(n)))
    n = len(CALLS); deliver(c, "777", attachments=[{"type": "audio", "payload": {"url": "x"}}])
    check("audio gets an honest answer", wait(lambda: any("only read text and images" in t for t in texts(n, "777"))))
    n = len(CALLS); deliver(c, "777", text="/status"); check("/status answers", wait(lambda: any("Idle" in t for t in texts(n, "777"))))
    n = len(CALLS); deliver(c, "777", text="/mode yolo"); check("/mode switches", wait(lambda: any("Mode: yolo" in t for t in texts(n, "777"))))
    n = len(CALLS); deliver(c, "777", text="/new"); check("/new starts a fresh chat", wait(lambda: any("Fresh chat" in t for t in texts(n, "777"))))
    n = len(CALLS); deliver(c, "777", text="/unpair")
    check("/unpair disconnects", wait(lambda: any("Unpaired" in t for t in texts(n, "777"))) and c.get("/api/instagram", headers=A).json()["paired"] == 0)
    check("secrets are hidden from the agent's env", all(k in open(os.path.join(ROOT, "backend/app/config.py")).read().split("SECRET_ENV_KEYS")[1] for k in ("INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_APP_SECRET")))
finally:
    for p in procs: p.terminate()

print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
