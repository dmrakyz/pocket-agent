"""End-to-end test: boots the mock model + the real backend, then drives it like the phone app does."""
import json
import os
import shutil
import subprocess
import sys
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = "/tmp/agent-e2e"
shutil.rmtree(DATA, ignore_errors=True)
env = os.environ | {
    "APP_PASSWORD": "pw123", "DATA_DIR": DATA, "ALLOWED_ORIGINS": "*",
    "PROVIDERS_JSON": json.dumps({"mock": {"base_url": "http://127.0.0.1:9101/v1", "api_key": "k"}}),
    "COMPACT_AT_TOKENS": "900",
}
procs = [
    subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_llm:app", "--port", "9101", "--log-level", "warning"],
                     cwd=os.path.join(ROOT, "tests"), env=env),
    subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", "9100", "--log-level", "warning"],
                     cwd=os.path.join(ROOT, "backend"), env=env),
]
B = "http://127.0.0.1:9100"
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


def wait_up():
    for _ in range(60):
        try:
            if httpx.get(B + "/health", timeout=1).status_code == 200 and httpx.get("http://127.0.0.1:9101/v1/models").status_code == 200:
                return
        except Exception:
            time.sleep(0.25)
    raise SystemExit("servers did not start")


def run_turn(c, sid, text, approve=None, timeout=60):
    """Send a message, stream events, auto-answer approvals. Returns the events."""
    r = c.post(f"/api/sessions/{sid}/messages", json={"text": text})
    assert r.status_code == 200, r.text
    evs, after = [], r.json()["seq"]
    t0 = time.time()
    with c.stream("GET", f"/api/sessions/{sid}/events", params={"after": 0}, timeout=timeout) as s:
        for line in s.iter_lines():
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:])
            evs.append(ev)
            if ev["type"] == "approval" and approve is not None:
                c.post(f"/api/sessions/{sid}/approve", json={"id": ev["id"], **approve(ev)})
            if ev["type"] == "done" or time.time() - t0 > timeout:
                break
    return evs


try:
    wait_up()
    c = httpx.Client(base_url=B, timeout=30)

    check("health", c.get("/health").json()["ok"])
    check("auth required", c.get("/api/sessions").status_code == 401)
    check("wrong password rejected", c.post("/api/login", json={"password": "nope"}).status_code == 401)
    tok = c.post("/api/login", json={"password": "pw123"}).json()["token"]
    c.headers["Authorization"] = "Bearer " + tok
    info = c.get("/api/info").json()
    check("info lists provider + default model (ultra preferred)", info["providers"] == ["mock"] and "ultra" in info["default_model"], str(info))
    check("git present", info["git"])
    ms = c.get("/api/models").json()["models"]
    check("model discovery", {m["id"] for m in ms} == {"mock/ultra-550b", "mock/lightning-30b"})
    check("model test ok", c.post("/api/models/test", json={"ref": "mock:mock/ultra-550b"}).json()["ok"])

    # --- plain chat
    s1 = c.post("/api/sessions", json={}).json()
    evs = run_turn(c, s1["id"], "hello there")
    types = [e["type"] for e in evs]
    check("streams reasoning + deltas + message + done", {"reasoning", "delta", "message", "done"} <= set(types), str(types))
    sess = c.get(f"/api/sessions/{s1['id']}").json()
    check("history persisted", [m["role"] for m in sess["session"]["messages"]] == ["user", "assistant"])
    check("reasoning kept for UI", "thinking" in sess["session"]["messages"][1].get("_reasoning", ""))
    time.sleep(0.5)
    check("auto title generated", c.get("/api/sessions").json()[0]["title"] == "Mock Chat Title", c.get("/api/sessions").json()[0]["title"])
    check("thinking param sent to non-nvidia = none", all(x is None for x in httpx.get("http://127.0.0.1:9101/seen").json()["thinking_param"]))

    # --- big multi-tool scenario with approvals (ask mode)
    s2 = c.post("/api/sessions", json={"title": "multi"}).json()
    asked = []

    def decide(ev):
        asked.append((ev["tool"], ev["args"].get("command", ev["args"].get("path", ""))))
        return {"allow": True, "always": ev["tool"] == "write"}
    evs = run_turn(c, s2["id"], "MULTI please", approve=decide, timeout=90)
    done = [e for e in evs if e["type"] == "done"]
    check("multi run finished", bool(done) and done[-1]["status"] == "done", str(done))
    tools_used = [m.get("_name") for m in c.get(f"/api/sessions/{s2['id']}").json()["session"]["messages"] if m["role"] == "tool"]
    check("all tools ran", tools_used == ["todo_write", "write", "read", "edit", "bash", "grep", "write", "bash", "git_sync"], str(tools_used))
    msgs = c.get(f"/api/sessions/{s2['id']}").json()["session"]["messages"]
    tm = {i: m for i, m in enumerate(m for m in msgs if m["role"] == "tool")}
    check("edit produced a diff", "+print('hello world')" in tm[3].get("_diff", ""), tm[3].get("_diff", ""))
    check("bash executed", "hello world" in tm[4]["content"], tm[4]["content"])
    check("path escape rejected", "outside the workspace" in tm[6]["content"], tm[6]["content"])
    check("environ read denied", tm[7]["content"].startswith("Denied"), tm[7]["content"])
    check("approvals asked for write/edit/bash", ("write", "hello.py") in asked and any(t == "bash" for t, _ in asked), str(asked))
    check("'always allow write' stops re-asking", sum(1 for t, _ in asked if t == "write") == 1, str(asked))
    check("todos stored", c.get(f"/api/sessions/{s2['id']}").json()["session"]["todos"][0]["content"] == "write hello")
    files = c.get(f"/api/sessions/{s2['id']}/files").json()["items"]
    check("files API", any(f["name"] == "hello.py" for f in files), str(files))
    check("file API", "hello world" in c.get(f"/api/sessions/{s2['id']}/file", params={"path": "hello.py"}).json()["text"])
    ws = os.path.join(DATA, "ws", s2["id"])
    log = subprocess.run(["git", "log", "--oneline"], cwd=ws, capture_output=True, text=True).stdout
    check("git commits made (checkpoint + sync)", len(log.strip().splitlines()) >= 2, log)

    # --- deny flow + feedback
    s3 = c.post("/api/sessions", json={"title": "deny"}).json()
    evs = run_turn(c, s3["id"], "MULTI deny", approve=lambda ev: {"allow": False, "note": "no thanks"}, timeout=60)
    m3 = [m for m in c.get(f"/api/sessions/{s3['id']}").json()["session"]["messages"] if m["role"] == "tool"]
    check("denied tool reports user note", "no thanks" in m3[1]["content"], str(m3[1]))

    # --- inline tool calls + bad JSON recovery
    s4 = c.post("/api/sessions", json={"mode": "yolo"}).json()
    run_turn(c, s4["id"], "INLINE test")
    m4 = c.get(f"/api/sessions/{s4['id']}").json()["session"]["messages"]
    check("inline <tool_call> parsed + executed", any("inline-ok" in (m.get("content") or "") for m in m4 if m["role"] == "tool"), str(m4)[-300:])
    run_turn(c, s4["id"], "BADJSON test")
    m4 = c.get(f"/api/sessions/{s4['id']}").json()["session"]["messages"]
    check("bad JSON args reported to model", any("not valid JSON" in (m.get("content") or "") for m in m4 if m["role"] == "tool"))

    # --- subagent
    run_turn(c, s4["id"], "SUB go")
    m4 = c.get(f"/api/sessions/{s4['id']}").json()["session"]["messages"]
    check("sub-agent returns report", any("subagent report" in (m.get("content") or "") for m in m4 if m["role"] == "tool"), str(m4[-2:]))

    # --- plan mode
    s5 = c.post("/api/sessions", json={"mode": "plan"}).json()
    evs = run_turn(c, s5["id"], "PLAN it", approve=lambda ev: {"allow": True, "mode": "auto"}, timeout=60)
    sess5 = c.get(f"/api/sessions/{s5['id']}").json()["session"]
    t5 = [m["content"] for m in sess5["messages"] if m["role"] == "tool"]
    check("plan mode blocked write", t5[0].startswith("Denied"), str(t5))
    check("plan approval switched mode", sess5["mode"] == "auto", sess5["mode"])
    check("write allowed after plan approval", os.path.exists(os.path.join(DATA, "ws", s5["id"], "a.txt")) and "planned" in open(os.path.join(DATA, "ws", s5["id"], "a.txt")).read())

    # --- rewind restores files
    msgs = c.get(f"/api/sessions/{s2['id']}").json()["session"]["messages"]
    r = c.post(f"/api/sessions/{s2['id']}/rewind", json={"index": 0}).json()
    check("rewind restores files + returns text", r["ok"] and "MULTI" in r["text"] and not os.path.exists(os.path.join(ws, "hello.py")), str(r))
    check("rewind truncates history", c.get(f"/api/sessions/{s2['id']}").json()["session"]["messages"] == [])

    # --- compaction (threshold 1500 tokens)
    s6 = c.post("/api/sessions", json={"mode": "yolo"}).json()
    for i in range(6):
        run_turn(c, s6["id"], f"BIG message {i}")
    m6 = c.get(f"/api/sessions/{s6['id']}").json()["session"]["messages"]
    check("auto-compaction produced a summary message", any(m.get("_summary") for m in m6), str([m["role"] for m in m6]))

    # --- stop + delete + rename
    r = c.patch(f"/api/sessions/{s6['id']}", json={"title": "renamed"}).json()
    check("rename", r["title"] == "renamed")
    check("delete", c.delete(f"/api/sessions/{s6['id']}").json()["ok"] and c.get(f"/api/sessions/{s6['id']}").status_code == 404)

    # --- persistence across restart: kill + restart backend, sessions come back from disk
    procs[1].terminate(); procs[1].wait()
    procs[1] = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", "9100", "--log-level", "warning"],
                                cwd=os.path.join(ROOT, "backend"), env=env)
    wait_up()
    c2 = httpx.Client(base_url=B, timeout=30, headers={"Authorization": "Bearer " + tok})
    check("token survives restart", c2.get("/api/sessions").status_code == 200)
    check("sessions survive restart", {s["id"] for s in c2.get("/api/sessions").json()} >= {s1["id"], s3["id"]})
    # workspace survives via git re-clone? (local-only here, so just check chat works after restart)
    evs = run_turn(c2, s1["id"], "hello again")
    check("chat works after restart", any(e["type"] == "done" and e["status"] == "done" for e in evs))

    # ---- parallel / per-model / nested sub-agents
    s7 = c2.post("/api/sessions", json={"mode": "yolo"}).json()
    run_turn(c2, s7["id"], "PARALLEL go", timeout=60)
    m7 = c2.get(f"/api/sessions/{s7['id']}").json()["session"]["messages"]
    tools7 = [m for m in m7 if m["role"] == "tool"]
    check("4 parallel sub-agent results returned in order", [m["_name"] for m in tools7] == ["task"] * 4, str(tools7)[:300])
    models_seen = httpx.get("http://127.0.0.1:9101/seen").json()["models"]
    final7 = [m for m in m7 if m["role"] == "assistant"][-1]["content"]
    check("per-subagent model: 'lightning' resolved", "model=mock/lightning-30b" in final7, final7)
    check("per-subagent model: default inherited", final7.count("model=mock/ultra-550b") == 3, final7)   # b, c, and fallback for the bogus name
    run_turn(c2, s7["id"], "NEST go", timeout=60)
    m7 = c2.get(f"/api/sessions/{s7['id']}").json()["session"]["messages"]
    final7 = [m for m in m7 if m["role"] == "assistant"][-1]["content"]
    check("nested sub-agents work 2 levels deep", "nest ok" in final7 and "leaf:" in final7, final7)
    check("3rd nesting level is blocked", "not available" in final7, final7)

    # ---- sub-agent approval is visible and works
    s8 = c2.post("/api/sessions", json={"mode": "ask"}).json()
    seen_labels = []
    def ok_all(ev):
        seen_labels.append(ev.get("label", ""))
        return {"allow": True}
    run_turn(c2, s8["id"], "SUBAPPROVE go", approve=ok_all, timeout=60)
    check("sub-agent approval carried a label", any(l.startswith("sub-agent") for l in seen_labels), str(seen_labels))
    check("sub-agent's approved command ran", os.path.exists(os.path.join(DATA, "ws", s8["id"], "made-by-sub.txt")))

    # ---- autopilot
    s9 = c2.post("/api/sessions", json={"mode": "yolo"}).json()
    check("autopilot patch", c2.patch(f"/api/sessions/{s9['id']}", json={"autopilot": True}).status_code == 200)
    r_eff = c2.patch(f"/api/sessions/{s9['id']}", json={"effort": "medium"}).json()
    check("effort medium saved", r_eff.get("effort") == "medium" and r_eff.get("thinking") is True, str(r_eff))
    r_eff = c2.patch(f"/api/sessions/{s9['id']}", json={"effort": "off"}).json()
    check("effort off disables thinking", r_eff.get("effort") == "off" and r_eff.get("thinking") is False, str(r_eff))
    r_eff = c2.patch(f"/api/sessions/{s9['id']}", json={"effort": "bogus"}).json()
    check("bad effort ignored", r_eff.get("effort") == "off")
    run_turn(c2, s9["id"], "AUTOP build", timeout=60)
    m9 = c2.get(f"/api/sessions/{s9['id']}").json()["session"]["messages"]
    last9 = [m for m in m9 if m["role"] == "assistant"][-1]["content"]
    check("autopilot nudged until DONE", last9.startswith("DONE:") and any(m.get("_synthetic") for m in m9), last9)
    s9b = c2.post("/api/sessions", json={"mode": "yolo"}).json()
    run_turn(c2, s9b["id"], "AUTOP build", timeout=60)
    m9b = c2.get(f"/api/sessions/{s9b['id']}").json()["session"]["messages"]
    check("without autopilot it stops after the first reply", [m for m in m9b if m["role"] == "assistant"][-1]["content"].startswith("partial"))

    # ---- files: upload (plain + zip + zip-slip), share_file, download
    import io, zipfile
    s10 = c2.post("/api/sessions", json={"mode": "yolo"}).json()
    up = c2.put(f"/api/sessions/{s10['id']}/upload", params={"name": "note.txt"}, content=b"hello upload").json()
    check("upload plain file", up["path"] == "uploads/note.txt" and up["size"] == 12, str(up))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("proj/a.txt", "A"); z.writestr("proj/sub/b.txt", "B"); z.writestr("../evil.txt", "pwn")
    up = c2.put(f"/api/sessions/{s10['id']}/upload", params={"name": "proj.zip"}, content=buf.getvalue()).json()
    ws10 = os.path.join(DATA, "ws", s10["id"])
    check("zip extracted", up["extracted_to"] == "uploads/proj" and os.path.exists(f"{ws10}/uploads/proj/proj/sub/b.txt"), str(up))
    check("zip-slip blocked", not os.path.exists(f"{ws10}/uploads/evil.txt") and not os.path.exists(f"{ws10}/evil.txt") and not os.path.exists(os.path.join(DATA, "ws", "evil.txt")))
    d = c2.get(f"/api/sessions/{s10['id']}/download", params={"path": "uploads/note.txt"})
    check("download file", d.content == b"hello upload" and "note.txt" in d.headers.get("content-disposition", ""))
    d = c2.get(f"/api/sessions/{s10['id']}/download", params={"path": "uploads/proj"})
    names = zipfile.ZipFile(io.BytesIO(d.content)).namelist()
    check("download folder as zip", "proj/a.txt" in names and not any(".git" in n for n in names), str(names))
    d = c2.get(f"/api/sessions/{s10['id']}/download", params={"path": ""})
    check("download whole workspace as zip", d.status_code == 200 and zipfile.is_zipfile(io.BytesIO(d.content)))
    check("download path traversal rejected", c2.get(f"/api/sessions/{s10['id']}/download", params={"path": "../../etc/passwd"}).status_code == 400)
    big = c2.put(f"/api/sessions/{s10['id']}/upload", params={"name": "big.bin"}, content=b"x" * (3 * 1024 * 1024))
    check("upload within limit ok", big.status_code == 200)
    run_turn(c2, s10["id"], "SHARE it", timeout=60)
    m10 = c2.get(f"/api/sessions/{s10['id']}").json()["session"]["messages"]
    check("share_file marks the result as downloadable", any(m.get("_file") == "report.txt" for m in m10 if m["role"] == "tool"), str([m.get("_file") for m in m10]))

    # --- preview tool end to end
    s11 = c2.post("/api/sessions", json={"mode": "yolo"}).json()
    run_turn(c2, s11["id"], "SHOWPAGE now", timeout=60)
    m11 = c2.get(f"/api/sessions/{s11['id']}").json()["session"]["messages"]
    check("preview tool marks the result", any(m.get("_preview") == "index.html" for m in m11 if m["role"] == "tool"), str([m.get("_preview") for m in m11]))
    pb11 = c2.post(f"/api/sessions/{s11['id']}/preview-token").json()["base"]
    check("agent-built page is served", "mock page" in httpx.get(B + pb11 + "index.html").text)

    # --- routines: create, run now, webhook, disable, delete
    MOCK = "mock:mock/ultra-550b"
    check("routine needs a prompt", c2.post("/api/routines", json={"name": "x"}).status_code == 400)
    rt = c2.post("/api/routines", json={"name": "Daily check", "prompt": "ROUTINE hello", "mode": "yolo", "model": MOCK,
                                        "schedule": {"kind": "every", "minutes": 5}, "tz": "Europe/Istanbul"}).json()
    check("routine created with next run + hook", rt["id"] and rt["next_run"] and rt["hook_path"].startswith("/hook/" + rt["id"]), str(rt))
    r1 = c2.post(f"/api/routines/{rt['id']}/run")
    check("routine runs now", r1.status_code == 200, r1.text)
    rsid = r1.json()["chat"]
    for _ in range(60):
        st = c2.get(f"/api/sessions/{rsid}").json()
        if not st["run"]["running"]:
            break
        time.sleep(0.3)
    time.sleep(0.5)
    rm = c2.get(f"/api/sessions/{rsid}").json()["session"]["messages"]
    check("routine chat got the prompt", any(m["role"] == "user" and "Routine 'Daily check'" in str(m["content"]) and "ROUTINE hello" in str(m["content"]) for m in rm))
    rl = {x["id"]: x for x in c2.get("/api/routines").json()["routines"]}
    check("routine records its last result", rl[rt["id"]]["last_status"] == "done" and rl[rt["id"]]["last_chat"] == rsid, str(rl[rt["id"]]))
    hk = httpx.post(B + rt["hook_path"], content=b"temp=31 door=open")
    check("webhook starts a run", hk.status_code == 200 and hk.json()["started"], hk.text)
    for _ in range(60):
        if not c2.get(f"/api/sessions/{hk.json()['chat']}").json()["run"]["running"]:
            break
        time.sleep(0.3)
    hm = c2.get(f"/api/sessions/{hk.json()['chat']}").json()["session"]["messages"]
    check("webhook payload is passed as untrusted data", any(m["role"] == "user" and "temp=31 door=open" in str(m["content"]) and "untrusted" in str(m["content"]) for m in hm))
    check("webhook with a wrong token is 404", httpx.post(B + "/hook/" + rt["id"] + "/wrong").status_code == 404)
    off = c2.patch(f"/api/routines/{rt['id']}", json={"enabled": False}).json()
    check("disabling clears next run", off["enabled"] is False and off["next_run"] is None)
    check("disabled routine's webhook is refused", httpx.post(B + rt["hook_path"]).status_code == 409)
    check("routine endpoints need login", httpx.Client(base_url=B).get("/api/routines").status_code == 401)
    check("routine delete works", c2.delete(f"/api/routines/{rt['id']}").json()["ok"] and not c2.get("/api/routines").json()["routines"])
    # connectors: only public https servers are accepted
    check("connector rejects http", c2.post("/api/connectors", json={"name": "a", "url": "http://example.com/mcp"}).status_code == 400)
    check("connector rejects private addresses", c2.post("/api/connectors", json={"name": "a", "url": "https://127.0.0.1/mcp"}).status_code == 400)
    check("connector list works", "connectors" in c2.get("/api/connectors").json())
    check("oauth connector rejects private addresses", c2.post("/api/connectors", json={"name": "a", "url": "https://127.0.0.1/mcp", "oauth": True}).status_code == 400)
    check("sign-in for an unknown connector is 404", c2.post("/api/connectors/nope/login", json={"base": "https://x.example"}).status_code == 404)
    cb = c2.get("/oauth/callback?state=forged&code=abc")
    check("oauth callback with a forged state is refused", cb.status_code == 200 and "Sign-in failed" in cb.text and "Connected" not in cb.text)
    check("oauth callback cancel page", "cancelled" in c2.get("/oauth/callback?error=access_denied").text)

    # --- 3D tool
    s12 = c2.post("/api/sessions", json={"mode": "yolo"}).json()
    run_turn(c2, s12["id"], "SHOWMODEL now", timeout=60)
    m12 = c2.get(f"/api/sessions/{s12['id']}").json()["session"]["messages"]
    check("view_3d marks the model", any(m.get("_model") == "cube.stl" for m in m12 if m["role"] == "tool"), str([(m.get("_model"), m.get("content", "")[:80]) for m in m12 if m["role"] == "tool"]))
    dl = c2.get(f"/api/sessions/{s12['id']}/download", params={"path": "cube.stl"})
    check("stl downloads and is non-empty", dl.status_code == 200 and len(dl.content) > 200)

    # --- push endpoints
    pk = c2.get("/api/push/key").json()
    check("push key served", pk["available"] and len(pk["key"]) >= 80, str(pk))
    check("bad subscription rejected", c2.post("/api/push/subscribe", json={"subscription": {"endpoint": "x"}}).status_code == 400)
    ok_sub = c2.post("/api/push/subscribe", json={"subscription": {"endpoint": "https://push.example/e2e", "keys": {"p256dh": "k", "auth": "a"}}}).json()
    check("subscription stored", ok_sub["devices"] == 1, str(ok_sub))
    check("presence accepted", c2.post("/api/presence", json={"visible": True}).json()["ok"])
    check("unsubscribe works", c2.post("/api/push/unsubscribe", json={"endpoint": "https://push.example/e2e"}).json()["devices"] == 0)
    check("push endpoints need login", httpx.Client(base_url=B).get("/api/push/key").status_code == 401)

    # --- live preview: scoped token, sandbox CSP, confinement, expiry
    up = c2.put(f"/api/sessions/{s10['id']}/upload", params={"name": "page.html", "extract": "false"}, content=b"<h1>hi preview</h1>").json()
    pb = c2.post(f"/api/sessions/{s10['id']}/preview-token").json()["base"]
    nc = httpx.Client(base_url=B, timeout=30)          # NO bearer token: the token lives in the URL
    r = nc.get(pb + "uploads/page.html")
    check("preview serves a workspace file", r.status_code == 200 and "hi preview" in r.text, str(r.status_code))
    check("preview is sandboxed", "sandbox" in r.headers.get("content-security-policy", "") and "allow-same-origin" not in r.headers.get("content-security-policy", ""))
    trav = [nc.get(pb + "%2e%2e/%2e%2e/%2e%2e/etc/passwd").status_code, nc.get(pb + "..%2f..%2f..%2fetc%2fpasswd").status_code]
    check("preview blocks traversal", all(c in (403, 404) for c in trav), str(trav))
    check("preview blocks .git", nc.get(pb + ".git/config").status_code in (403, 404))
    check("preview token is per-chat", nc.get(pb.replace(s10["id"], s9["id"]) + "uploads/page.html").status_code == 403)
    bad = pb[:-5] + "xxxx/"
    check("preview rejects a forged token", nc.get(bad + "uploads/page.html").status_code == 403)
    check("preview needs a real login to mint", nc.post(f"/api/sessions/{s10['id']}/preview-token").status_code == 401)

    # --- eval pipeline (mock only knows task 1)
    eid = c2.post("/api/eval", json={"model": "mock:mock/ultra-550b"}).json()["id"]
    for _ in range(80):
        ev = c2.get(f"/api/eval/{eid}").json()
        if ev["status"] == "done":
            break
        time.sleep(0.5)
    check("eval ran all tasks", ev["status"] == "done" and len(ev["results"]) == 4, str(ev))
    check("eval task 1 passes with scripted model", ev["results"][0]["passed"], str(ev["results"][0]))
    check("eval exposes task list + clock", ev.get("tasks") and len(ev["tasks"]) == 4 and ev.get("now"), str(ev.keys()))
    check("eval-latest returns it", c2.get("/api/eval-latest").json()["eval"]["id"] == eid)
    eid2 = c2.post("/api/eval", json={"model": "mock:mock/ultra-550b"}).json()["id"]
    check("eval stop accepted", c2.post(f"/api/eval/{eid2}/stop").status_code == 200)
    for _ in range(40):
        ev2 = c2.get(f"/api/eval/{eid2}").json()
        if ev2["status"] != "running":
            break
        time.sleep(0.5)
    check("eval stops early", ev2["status"] == "stopped" and len(ev2["results"]) < 4, str(ev2))
finally:
    for p in procs:
        p.terminate()

print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
