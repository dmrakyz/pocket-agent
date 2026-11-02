"""Agent as MCP server end to end: JSON-RPC handshake, tool list, ask_agent, chat continuation, auth and revocation."""
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
DATA = "/tmp/agent-mcps"
shutil.rmtree(DATA, ignore_errors=True)
APP_PORT, LLM_PORT = 9150, 9151
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
    def rpc(path, method, params=None, id_=1, headers=None):
        m = {"jsonrpc": "2.0", "method": method}
        if id_ is not None: m["id"] = id_
        if params is not None: m["params"] = params
        return c.post(path, json=m, headers=headers or {})
    check("no token yet: refused", rpc("/mcp/nope", "ping").status_code == 401)
    t = c.post("/api/mcp-server/token", headers=A, json={}).json()["token"]
    st = c.get("/api/mcp-server/status", headers=A).json()
    check("token shown once, only a hint stored", t.startswith("mcp_") and st["enabled"] and t not in json.dumps(st), str(st))
    P = "/mcp/" + t
    r = rpc(P, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}).json()
    check("initialize", r["result"]["serverInfo"]["name"] == "pocket-agent" and "tools" in r["result"]["capabilities"], str(r))
    check("initialized notification gets 202", rpc(P, "notifications/initialized", id_=None).status_code == 202)
    names = [x["name"] for x in rpc(P, "tools/list").json()["result"]["tools"]]
    check("tools/list", names == ["ask_agent", "list_chats", "read_chat"], str(names))
    r = rpc(P, "tools/call", {"name": "ask_agent", "arguments": {"prompt": "hello over mcp"}}).json()["result"]
    txt = r["content"][0]["text"]
    check("ask_agent answers and returns a chat id", not r["isError"] and "You said" in txt and "chat_id:" in txt, txt[:300])
    cid = txt.rsplit("chat_id:", 1)[1].strip(" ]\n")
    r = rpc(P, "tools/call", {"name": "ask_agent", "arguments": {"prompt": "and again", "chat_id": cid}}).json()["result"]
    check("chat_id continues the same chat", not r["isError"] and cid in r["content"][0]["text"], str(r)[:300])
    r = rpc(P, "tools/call", {"name": "read_chat", "arguments": {"chat_id": cid}}).json()["result"]["content"][0]["text"]
    check("read_chat shows both turns", "hello over mcp" in r and "and again" in r, r[:300])
    r = rpc(P, "tools/call", {"name": "list_chats", "arguments": {}}).json()["result"]["content"][0]["text"]
    check("list_chats lists it with an MCP title and the model", cid in r and "MCP" in r and "[mock:" in r, r[:300])
    r = rpc(P, "tools/call", {"name": "ask_agent", "arguments": {"prompt": "x", "chat_id": "zzz"}}).json()["result"]
    check("unknown chat is a tool error, not a crash", r["isError"] is True)
    r = rpc(P, "tools/call", {"name": "ask_agent", "arguments": {"prompt": "EMBED please"}}).json()["result"]
    check("approvals are refused instead of hanging", r["content"][0]["text"], str(r)[:200])
    check("unknown method is a JSON-RPC error", rpc(P, "nope").json()["error"]["code"] == -32601)
    check("bearer header works on /mcp", rpc("/mcp", "ping", headers={"Authorization": "Bearer " + t}).json()["result"] == {})
    check("batch works", len(c.post(P, json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "id": 2, "method": "ping"}]).json()) == 2)
    check("bad JSON is a parse error", c.post(P, content=b"{nope", headers={"content-type": "application/json"}).status_code == 400)
    check("GET is 405 (no server stream)", c.get(P).status_code == 405)
    check("app login token is not an MCP token", rpc("/mcp", "ping", headers=A).status_code == 401)
    t2 = c.post("/api/mcp-server/token", headers=A, json={}).json()["token"]
    check("new token kills the old one", rpc(P, "ping").status_code == 401 and rpc("/mcp/" + t2, "ping").status_code == 200)
    c.delete("/api/mcp-server", headers=A)
    check("revoking cuts it off", rpc("/mcp/" + t2, "ping").status_code == 401)
finally:
    for p in procs: p.terminate()

print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
