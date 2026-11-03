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
    "APP_PASSWORD": "pw123", "DATA_DIR": DATA, "ALLOWED_ORIGINS": "*", "BROWSER_ALLOW_LOCAL": "1",
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

    # --- sense of time: stamps on what the model sees, in the user's zone; its own copied stamp is stripped
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _Z
    stz = c.post("/api/sessions", json={"mode": "yolo"}).json()
    r_tz = c.post(f"/api/sessions/{stz['id']}/messages", json={"text": "TIMECHECK please", "tz": "Asia/Tokyo"})
    for _ in range(100):
        sess_tz = c.get(f"/api/sessions/{stz['id']}").json()
        if not sess_tz["run"].get("running") and sess_tz["session"]["messages"][-1]["role"] == "assistant":
            break
        time.sleep(0.2)
    out_tz = sess_tz["session"]["messages"][-1]["content"]
    jst = _dt.now(_Z("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M")
    check("the model sees a timestamp on the user's message, in the user's own zone", f"stamp={jst}" in out_tz or f"stamp={_dt.fromtimestamp(time.time() - 60, _Z('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M')}" in out_tz, out_tz)
    check("the system prompt says what time it is and where", "tz=Asia/Tokyo" in out_tz and "now=" in out_tz and "now=None" not in out_tz, out_tz)
    check("tool results carry a finish time, and the prompt explains the stamps", "tool=yes" in out_tz and "note=True" in out_tz, out_tz)
    check("a timestamp the model copies onto its own reply is stripped before saving", not out_tz.startswith("[2020-01-01"), out_tz)
    check("the stored chat itself is not polluted with stamps", not str(sess_tz["session"]["messages"][0]["content"]).startswith("[20"), str(sess_tz["session"]["messages"][0]["content"])[:40])

    # --- self-review: a fresh reviewer reads the diff; real problems go back once, LGTM changes nothing, off by default
    def rv_turn(prompt, on):
        sr = c.post("/api/sessions", json={"mode": "yolo"}).json()
        if on:
            c.patch(f"/api/sessions/{sr['id']}", json={"review": True})
        evs_r = run_turn(c, sr["id"], prompt, timeout=90)
        return sr["id"], evs_r, c.get(f"/api/sessions/{sr['id']}").json()["session"]
    sid_r, evs_r, ses_r = rv_turn("RVWBAD fix it", True)
    revs = [m for m in ses_r["messages"] if m["role"] == "user" and str(m.get("content")).startswith("[review]")]
    check("session reports the review switch", ses_r.get("review") is True)
    check("reviewer problems go back to the agent exactly once", len(revs) == 1 and "ignores negative numbers" in revs[0]["content"], str([str(m.get("content"))[:60] for m in ses_r["messages"]]))
    check("the agent answered after fixing, and the reviewer ran as a sub-agent", ses_r["messages"][-1]["content"] == "Fixed after review." and any(e.get("type") == "sub_start" and e.get("label") == "review" for e in evs_r), str(ses_r["messages"][-1])[:150])
    check("a visible notice tells the user a review happened", any(e.get("type") == "notice" and "Reviewing the changes" in e.get("text", "") for e in evs_r))
    sid_r2, evs_r2, ses_r2 = rv_turn("RVWOK fine", True)
    check("LGTM from the reviewer adds nothing and the answer stands", not any(str(m.get("content")).startswith("[review]") for m in ses_r2["messages"]) and ses_r2["messages"][-1]["content"] == "Done." and any("no problems found" in e.get("text", "") for e in evs_r2 if e.get("type") == "notice"))
    sid_r3, evs_r3, ses_r3 = rv_turn("RVWBAD but review is off", False)
    check("self-review is off by default: no reviewer, no extra turn", ses_r3.get("review") is False and not any(e.get("type") == "sub_start" for e in evs_r3) and ses_r3["messages"][-1]["content"] == "Done.", str(ses_r3["messages"][-1])[:100])

    # --- verify gate: code changed but never run -> sent back; after a real run it passes; stubborn model gets flagged
    sv = c.post("/api/sessions", json={"mode": "yolo"}).json()
    run_turn(c, sv["id"], "VGATE please", timeout=60)
    mv = c.get(f"/api/sessions/{sv['id']}").json()["session"]["messages"]
    check("verify gate sent the unverified work back once", sum(1 for x in mv if x["role"] == "user" and str(x.get("content")).startswith("[system] You changed code")) == 1, str([str(x.get("content"))[:60] for x in mv]))
    check("after it ran the code the final answer is accepted without a warning", mv[-1]["role"] == "assistant" and not mv[-1].get("_unverified") and any(x.get("role") == "tool" and x["content"].strip() == "5" for x in mv), str(mv[-3:])[:300])
    sv2 = c.post("/api/sessions", json={"mode": "yolo"}).json()
    evs = run_turn(c, sv2["id"], "VGATEBAD please", timeout=60)
    mv2 = c.get(f"/api/sessions/{sv2['id']}").json()["session"]["messages"]
    check("a model that never runs it is nudged at most twice, then flagged unverified", sum(1 for x in mv2 if x["role"] == "user" and str(x.get("content")).startswith("[system] You changed code")) == 2 and mv2[-1].get("_unverified") is True, str([str(x.get("content"))[:50] for x in mv2]))

    # --- ask_user: the agent asks, taps come back, only offered options get through; plan mode may ask too
    sq = c.post("/api/sessions", json={"mode": "plan"}).json()
    seen = []

    def tap(ev):
        seen.append(ev)
        return {"allow": True, "answers": [{"choice": ["SQLite (Recommended)", "HACKED"]}, {"choice": ["Auth", "Export", "Nope"], "other": "plus logging"}]}
    run_turn(c, sq["id"], "ASKUSER please", approve=tap, timeout=60)
    msgs_q = c.get(f"/api/sessions/{sq['id']}").json()["session"]["messages"]
    tool_q = [m["content"] for m in msgs_q if m["role"] == "tool"]
    check("ask_user raised one question event with both questions (plan mode allows it)", len(seen) == 1 and seen[0]["tool"] == "ask_user" and len(seen[0]["args"]["questions"]) == 2, str(seen)[:200])
    check("single-select kept the offered pick and dropped made-up text", "Which database? → SQLite (Recommended)" in tool_q[0] and "HACKED" not in tool_q[0], tool_q[0])
    check("multi-select keeps only offered labels, adds the typed Other", "Which extras? → Auth; Export; Other: plus logging" in tool_q[0] and "Nope" not in tool_q[0], tool_q[0])
    sq2 = c.post("/api/sessions", json={"mode": "yolo"}).json()
    run_turn(c, sq2["id"], "ASKUSER skip", approve=lambda ev: {"allow": False, "note": "the user skipped the question"}, timeout=60)
    t_skip = [m["content"] for m in c.get(f"/api/sessions/{sq2['id']}").json()["session"]["messages"] if m["role"] == "tool"][0]
    check("skipping tells the agent to assume a default or ask in text", "could not be answered" in t_skip and "most reasonable" in t_skip, t_skip)
    sq3 = c.post("/api/sessions", json={"mode": "yolo"}).json()
    run_turn(c, sq3["id"], "ASKBAD now", timeout=60)
    t_bad = [m["content"] for m in c.get(f"/api/sessions/{sq3['id']}").json()["session"]["messages"] if m["role"] == "tool"][0]
    check("a malformed question is bounced back to the model, never shown", "2 to 4 distinct options" in t_bad, t_bad)

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
    # embedded files/images in answers
    es = c2.post("/api/sessions", json={"mode": "yolo"}).json()["id"]
    evs = run_turn(c2, es, "EMBED please")
    hist = c2.get(f"/api/sessions/{es}").json()["session"]["messages"]
    final = [m for m in hist if m["role"] == "assistant" and not m.get("tool_calls")][-1]["content"]
    check("answer that links a missing file is sent back to fix", any(m["role"] == "user" and (m["content"] if isinstance(m["content"], str) else "").startswith("[system] Your answer links files that do not exist") for m in hist))
    check("final answer embeds real files only", "ghost.png" not in final and "ws:pics/a.png" in final and "ws:report.txt" in final, final)
    st = c2.get(f"/api/sessions/{es}/stat", params=[("paths", "pics/a.png"), ("paths", "report.txt"), ("paths", "pics"), ("paths", "nope.png"), ("paths", "../x"), ("paths", ".git/config")]).json()["files"]
    kinds = {f["path"]: (f.get("kind"), f["exists"]) for f in st}
    check("stat reports kinds and existence", kinds["pics/a.png"] == ("image", True) and kinds["report.txt"] == ("text", True) and kinds["pics"] == ("folder", True) and kinds["nope.png"][1] is False and kinds["../x"][1] is False and kinds[".git/config"][1] is False, str(kinds))
    check("stat gives sizes", next(f for f in st if f["path"] == "report.txt")["size"] == 6)
    check(".git is not downloadable", c2.get(f"/api/sessions/{es}/download", params={"path": ".git/config"}).status_code == 400)
    # live browser view
    import http.server, threading
    class PH(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(b"<html><title>E2E page</title><body style='background:#c33'><h1>hello browser</h1></body></html>")
        def log_message(self, *a): pass
    psrv = http.server.ThreadingHTTPServer(("127.0.0.1", 9334), PH); threading.Thread(target=psrv.serve_forever, daemon=True).start()
    bs = c2.post("/api/sessions", json={}).json()["id"]
    check("browser info empty before opening", c2.get(f"/api/sessions/{bs}/browser").json()["info"] is None)
    check("browser shot is empty (204) before opening", c2.get(f"/api/sessions/{bs}/browser/shot").status_code == 204)
    r = c2.post(f"/api/sessions/{bs}/browser/act", json={"type": "goto", "url": "http://127.0.0.1:9334/"}, timeout=60)
    check("browser goto works", r.status_code == 200 and r.json()["info"]["title"] == "E2E page", r.text[:200])
    shot = c2.get(f"/api/sessions/{bs}/browser/shot")
    check("browser shot is a jpeg", shot.status_code == 200 and shot.content[:2] == b"\xff\xd8" and len(shot.content) > 2000)
    check("browser rejects unknown action", c2.post(f"/api/sessions/{bs}/browser/act", json={"type": "explode"}).status_code == 400)
    check("browser view needs auth", httpx.get(B + f"/api/sessions/{bs}/browser/shot").status_code == 401)
    import json as _j
    with c2.stream("GET", f"/api/sessions/{bs}/browser/stream?w=390&h=700", timeout=30) as sr:
        got = None
        for ln in sr.iter_lines():
            if ln:
                m = _j.loads(ln)
                if m.get("f"): got = m; break
    check("live stream sends a jpeg frame with page meta", bool(got) and len(got["f"]) > 1000 and got["url"].startswith("http://127.0.0.1:9334") and got["w"] == 1000 and 1500 < got["h"] < 1900, str(got)[:200] if got else "no frame")
    check("browser input tap moves the agent-visible cursor", c2.post(f"/api/sessions/{bs}/browser/input", json={"type": "tap", "x": 50, "y": 60}).status_code == 200)
    check("browser input rejects unknown", c2.post(f"/api/sessions/{bs}/browser/input", json={"type": "nope"}).status_code == 400)
    check("stream needs auth", httpx.get(B + f"/api/sessions/{bs}/browser/stream").status_code == 401)
    c2.post(f"/api/sessions/{bs}/browser/act", json={"type": "close"})
    check("browser closes", c2.get(f"/api/sessions/{bs}/browser").json()["info"] is None)
    psrv.shutdown()
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
    eid = c2.post("/api/eval", json={"model": "mock:mock/ultra-550b", "effort": "medium"}).json()["id"]
    for _ in range(80):
        ev = c2.get(f"/api/eval/{eid}").json()
        if ev["status"] == "done":
            break
        time.sleep(0.5)
    check("eval ran all tasks", ev["status"] == "done" and len(ev["results"]) == 7, str(ev))
    check("eval task 1 passes with scripted model", ev["results"][0]["passed"], str(ev["results"][0]))
    check("eval exposes task list + clock", ev.get("tasks") and len(ev["tasks"]) == 7 and ev.get("now"), str(ev.keys()))
    check("eval task 5 (build an app) passes with scripted model and leaves the drawer clean", ev["results"][6]["passed"] and c2.get("/api/apps").json()["apps"] == [], str(ev["results"][6]) + str(c2.get("/api/apps").json()))
    check("eval task 'Knows its own tools' passes only after the guard sends the wrong refusal back", ev["results"][5]["name"] == "Knows its own tools" and ev["results"][5]["passed"], str(ev["results"][5]))
    check("eval records the effort level and suite it ran with", ev["effort"] == "medium" and ev["suite"] == "standard", str(ev.get("effort")))
    hist = c2.get("/api/eval-history").json()["history"]
    check("finished run is kept in the comparison history", hist and hist[0]["id"] == eid and hist[0]["effort"] == "medium" and hist[0]["passed"] >= 2 and hist[0]["total"] == 7, str(hist[:1]))
    check("eval-latest returns it", c2.get("/api/eval-latest").json()["eval"]["id"] == eid)
    eh = c2.post("/api/eval", json={"model": "mock:mock/ultra-550b", "suite": "hard", "effort": "off"}).json()["id"]
    for _ in range(240):
        evh = c2.get(f"/api/eval/{eh}").json()
        if evh["status"] != "running":
            break
        time.sleep(0.5)
    check("the hard suite runs all 12 tasks through the same pipeline (the scripted mock can't solve them)", evh["status"] == "done" and len(evh["results"]) == 12 and evh["suite"] == "hard" and evh["effort"] == "off" and not any(r["passed"] for r in evh["results"]), str(evh)[:300])
    check("every hard result says why it failed", all(r["note"] for r in evh["results"]), str([r["note"] for r in evh["results"]])[:200])
    check("history keeps both suites", {h["suite"] for h in c2.get("/api/eval-history").json()["history"]} >= {"standard", "hard"})
    cat = c2.get("/api/eval-tasks").json()["suites"]
    check("task catalog lists both suites", len(cat["standard"]) == 7 and len(cat["hard"]) == 12 and "Knows its own tools" in cat["standard"], str(cat)[:200])
    es = c2.post("/api/eval", json={"model": "mock:mock/ultra-550b", "effort": "off", "skip": ["Compile C++", "Build an app", "Explore codebase", "nonsense"]}).json()["id"]
    for _ in range(120):
        evs = c2.get(f"/api/eval/{es}").json()
        if evs["status"] != "running":
            break
        time.sleep(0.5)
    check("excluded tasks are not run", evs["status"] == "done" and evs["total"] == 4 and len(evs["results"]) == 4 and "Compile C++" not in evs["tasks"] and sorted(evs["skipped"]) == ["Build an app", "Compile C++", "Explore codebase"], str(evs)[:300])
    check("an eval with every task excluded is refused", c2.post("/api/eval", json={"model": "mock:mock/ultra-550b", "skip": cat["standard"]}).status_code == 400)
    eid2 = c2.post("/api/eval", json={"model": "mock:mock/ultra-550b", "effort": "turbo"}).json()["id"]
    check("eval stop accepted", c2.post(f"/api/eval/{eid2}/stop").status_code == 200)
    for _ in range(40):
        ev2 = c2.get(f"/api/eval/{eid2}").json()
        if ev2["status"] != "running":
            break
        time.sleep(0.5)
    check("a made-up effort falls back to high", c2.get(f"/api/eval/{eid2}").json()["effort"] == "high")
    check("eval stops early", ev2["status"] == "stopped" and len(ev2["results"]) < 5, str(ev2))
finally:
    for p in procs:
        p.terminate()

print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
