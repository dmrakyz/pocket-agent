"""Telegram bridge end to end: real backend + mock model + a fake Telegram server that records every call."""
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
DATA = "/tmp/agent-tg"
shutil.rmtree(DATA, ignore_errors=True)
CALLS: list = []
FAKE_PORT, APP_PORT, LLM_PORT = 9112, 9110, 9111

from fastapi import FastAPI, Request          # noqa: E402
from fastapi.responses import JSONResponse, Response   # noqa: E402
import uvicorn                                 # noqa: E402

fake = FastAPI()
JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb004300") + b"\x00" * 64 + b"\xff\xd9"


@fake.post("/bot{tok}/{method}")
async def tg_call(tok: str, method: str, request: Request):
    ct = request.headers.get("content-type", "")
    body = await request.body()
    params = json.loads(body) if "json" in ct and body else {"_bytes": len(body)}
    CALLS.append((method, params))
    if method == "getMe":
        return {"ok": True, "result": {"username": "pocket_test_bot"}}
    if method == "getFile":
        return {"ok": True, "result": {"file_id": params.get("file_id"), "file_path": "photos/p.jpg", "file_size": len(JPEG)}}
    return {"ok": True, "result": True}


@fake.get("/file/bot{tok}/photos/p.jpg")
async def tg_file(tok: str):
    return Response(JPEG, media_type="image/jpeg")


threading.Thread(target=lambda: uvicorn.run(fake, port=FAKE_PORT, log_level="warning"), daemon=True).start()

env = os.environ | {
    "APP_PASSWORD": "pw123", "DATA_DIR": DATA, "ALLOWED_ORIGINS": "*",
    "PROVIDERS_JSON": json.dumps({"mock": {"base_url": f"http://127.0.0.1:{LLM_PORT}/v1", "api_key": "k"}}),
    "TELEGRAM_BOT_TOKEN": "tgtoken", "TELEGRAM_API_BASE": f"http://127.0.0.1:{FAKE_PORT}", "PUBLIC_URL": "https://example.test",
}
procs = [
    subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_llm:app", "--port", str(LLM_PORT), "--log-level", "warning"], cwd=os.path.join(ROOT, "tests"), env=env),
    subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(APP_PORT), "--log-level", "warning"], cwd=os.path.join(ROOT, "backend"), env=env),
]
B = f"http://127.0.0.1:{APP_PORT}"
fails = []
SECRET = hashlib.sha256(b"salt-v1:pw123").hexdigest()
mac = lambda label: hmac.new(SECRET.encode(), ("telegram-" + label).encode(), hashlib.sha256).hexdigest()[:32]
HOOK, HDR = f"/telegram/{mac('path')}", {"x-telegram-bot-api-secret-token": mac("header")}


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


def texts(since=0):
    return [p.get("text", "") for m, p in CALLS[since:] if m == "sendMessage"]


def wait(pred, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.2)
    return False


uid = [100]
def update(c, chat, **msg):
    uid[0] += 1
    return c.post(HOOK, headers=HDR, json={"update_id": uid[0], "message": {"chat": {"id": chat, "type": "private"}, **msg}})


try:
    for _ in range(80):
        try:
            if httpx.get(B + "/health", timeout=1).status_code == 200: break
        except Exception: time.sleep(0.25)
    c = httpx.Client(base_url=B, timeout=30)
    check("startup registered the webhook with a secret token", wait(lambda: any(m == "setWebhook" for m, _ in CALLS)))
    sw = next(p for m, p in CALLS if m == "setWebhook")
    check("webhook URL carries the secret path", sw["url"] == "https://example.test" + HOOK and sw["secret_token"] == mac("header"), str(sw))
    check("only messages and callbacks are requested", set(sw["allowed_updates"]) == {"message", "callback_query"})

    check("wrong secret header is rejected", c.post(HOOK, headers={"x-telegram-bot-api-secret-token": "nope"}, json={"update_id": 1}).status_code == 404)
    check("wrong path is rejected", c.post("/telegram/deadbeef", headers=HDR, json={"update_id": 1}).status_code == 404)

    n = len(CALLS); update(c, 42, text="hello there")
    check("unpaired chat is told the bot is private", wait(lambda: any("private" in t for t in texts(n))))
    tok = c.post("/api/login", json={"password": "pw123"}).json()["token"]; A = {"Authorization": "Bearer " + tok}
    check("and nothing ran for it", c.get("/api/sessions", headers=A).json() in ([], {"sessions": []}) or not c.get("/api/sessions", headers=A).json().get("sessions", c.get("/api/sessions", headers=A).json()))
    r = c.post("/api/telegram/connect", headers=A, json={"base": "https://app.example"}).json()
    check("connect returns a pairing code and the bot name", len(r.get("code", "")) == 6 and r["bot"] == "pocket_test_bot", str(r))
    n = len(CALLS); update(c, 42, text="/pair WRONG1")
    check("wrong pairing code refused", wait(lambda: any("wrong or expired" in t for t in texts(n))))
    n = len(CALLS); update(c, 42, text=f"/pair {r['code']}")
    check("right code pairs the chat", wait(lambda: any("Paired" in t for t in texts(n))))
    n = len(CALLS); update(c, 42, text=f"/pair {r['code']}")
    check("a code works once", wait(lambda: any("wrong or expired" in t for t in texts(n))))
    check("status shows one paired chat", c.get("/api/telegram", headers=A).json()["paired"] == 1)

    n = len(CALLS); update(c, 42, text="hello there")
    check("paired chat gets the agent's answer", wait(lambda: any("You said: hello there" in t for t in texts(n))), str(texts(n)))
    check("typing indicator was shown", any(m == "sendChatAction" for m, _ in CALLS[n:]))

    n = len(CALLS); update(c, 42, text="EMBED please")
    check("approval arrives as Allow/Deny buttons", wait(lambda: any(m == "sendMessage" and "inline_keyboard" in json.dumps(p.get("reply_markup", {})) for m, p in CALLS[n:])))
    kb = next(p["reply_markup"] for m, p in CALLS[n:] if m == "sendMessage" and p.get("reply_markup"))
    allow = kb["inline_keyboard"][0][0]["callback_data"]
    check("approval text names the command", any("bash" in t and "Approve?" in t for t in texts(n)))
    uid[0] += 1
    c.post(HOOK, headers=HDR, json={"update_id": uid[0], "callback_query": {"id": "cb1", "data": "ap:ffffffff:1", "message": {"chat": {"id": 42}, "message_id": 5}}})
    time.sleep(0.5); check("an unknown callback token does nothing", not any(m == "sendPhoto" for m, _ in CALLS[n:]))
    uid[0] += 1
    c.post(HOOK, headers=HDR, json={"update_id": uid[0], "callback_query": {"id": "cb2", "data": allow, "message": {"chat": {"id": 99}, "message_id": 5}}})
    time.sleep(0.5); check("a callback from an unpaired chat is ignored", not any(m == "sendPhoto" for m, _ in CALLS[n:]))
    uid[0] += 1
    c.post(HOOK, headers=HDR, json={"update_id": uid[0], "callback_query": {"id": "cb3", "data": allow, "message": {"chat": {"id": 42}, "message_id": 5}}})
    check("tapping Allow lets the run finish and sends the images", wait(lambda: sum(m == "sendPhoto" for m, _ in CALLS[n:]) >= 2), str([m for m, _ in CALLS[n:]]))
    check("report file is sent as a document", wait(lambda: any(m == "sendDocument" for m, _ in CALLS[n:])))
    check("embed markup is stripped from the text", not any("ws:" in t for t in texts(n)), str(texts(n)))
    check("the callback was answered and the buttons removed", any(m == "answerCallbackQuery" for m, _ in CALLS[n:]) and any(m == "editMessageReplyMarkup" for m, _ in CALLS[n:]))

    n = len(CALLS); update(c, 42, caption="what is this", photo=[{"file_id": "small"}, {"file_id": "big"}])
    check("photos are downloaded and answered", wait(lambda: any(m == "getFile" and p.get("file_id") == "big" for m, p in CALLS[n:])) and wait(lambda: any("You said" in t for t in texts(n))), str(texts(n)))
    n = len(CALLS); update(c, 42, voice={"file_id": "v"})
    check("voice notes get an honest answer", wait(lambda: any("can't transcribe" in t for t in texts(n))))
    n = len(CALLS); update(c, 42, text="/status")
    check("/status answers", wait(lambda: any("Idle" in t for t in texts(n))))
    n = len(CALLS); update(c, 42, text="/mode yolo"); check("/mode switches", wait(lambda: any("Mode: yolo" in t for t in texts(n))))
    n = len(CALLS); update(c, 42, text="/new"); check("/new starts a fresh chat", wait(lambda: any("Fresh chat" in t for t in texts(n))))
    n = len(CALLS); update(c, 42, text="/unpair")
    check("/unpair disconnects", wait(lambda: any("Unpaired" in t for t in texts(n))) and c.get("/api/telegram", headers=A).json()["paired"] == 0)
    n = len(CALLS); update(c, 42, text="hello again")
    check("after unpairing the chat is refused again", wait(lambda: any("private" in t for t in texts(n))))
    check("the bot token never appears in agent-visible env", "TELEGRAM_BOT_TOKEN" in open(os.path.join(ROOT, "backend/app/config.py")).read().split("SECRET_ENV_KEYS")[1])
finally:
    for p in procs: p.terminate()

print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
