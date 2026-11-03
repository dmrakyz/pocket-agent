"""Watchers: url checks (baseline, change, contains/absent, failures), quiet prompt watchers, tools, approvals."""
import asyncio, http.server, os, shutil, sys, threading, time
shutil.rmtree("/tmp/agent-watch", ignore_errors=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.update({"APP_PASSWORD": "pw", "DATA_DIR": "/tmp/agent-watch"})
PAGE = {"html": "<html><title>Shop</title><body><p>Price: 100 TL</p><p>Status: sold out</p></body></html>", "code": 200}
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(PAGE["code"]); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(PAGE["html"].encode())
    def log_message(self, *a): pass
threading.Thread(target=http.server.ThreadingHTTPServer(("127.0.0.1", 9345), H).serve_forever, daemon=True).start()
from pathlib import Path
from app import tools as T, watchers as W, routines, perms, push, kv
T._check_public = lambda url: None            # the test page is on localhost
sent = []
async def fake_notify(title, body, **kw): sent.append((title, body)); return 1
push.notify = fake_notify
fails = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: fails.append(n)
URL = "http://127.0.0.1:9345/"

async def main():
    ctx = T.Ctx({"id": "w1", "mode": "ask"}, Path("/tmp"), None)
    r = await T.t_watch_create(ctx, {"kind": "url", "name": "Shop price", "url": URL, "every_minutes": 5})
    w = W.all_watchers()[0]
    check("watch_create stores a url watcher", "Watching" in r and w["every"] == 5 and w["condition"] == "change")
    await W.run_due(time.time() + 1)
    check("first check only sets the baseline (no notification)", sent == [] and W.all_watchers()[0]["last_hash"])
    await W.run_due(time.time() + 30)
    check("nothing changed and it isn't due yet: still quiet", sent == [])
    PAGE["html"] = PAGE["html"].replace("100 TL", "80 TL")
    await W.run_due(time.time() + 600)
    check("a change notifies once, and says what changed", len(sent) == 1 and "80 TL" in sent[0][1] and sent[0][0].startswith("👁"), str(sent))
    await W.run_due(time.time() + 1300)
    check("an unchanged page after that is quiet again", len(sent) == 1)
    # contains / absent
    sent.clear()
    await T.t_watch_create(ctx, {"kind": "url", "name": "Restock", "url": URL, "condition": "absent:sold out", "every_minutes": 5})
    t = time.time() + 2000
    await W.run_due(t)
    check("absent: with the phrase still there, no alert", sent == [], str(sent))
    PAGE["html"] = PAGE["html"].replace("sold out", "in stock")
    await W.run_due(t + 700)
    check("absent: alert when the phrase disappears", any("no longer mentions" in b for _, b in sent), str(sent))
    sent.clear()
    await T.t_watch_create(ctx, {"kind": "url", "name": "Sale", "url": URL, "condition": "contains:flash sale", "every_minutes": 5})
    t += 5000; await W.run_due(t)
    PAGE["html"] = PAGE["html"].replace("<p>Price", "<p>FLASH SALE today</p><p>Price")
    await W.run_due(t + 700)
    check("contains: alert when the phrase appears (case-insensitive)", any("now mentions" in b for _, b in sent), str(sent))
    # failures
    sent.clear(); PAGE["code"] = 500
    for k in range(1, 5):
        await W.run_due(t + 700 + k * 700)
    check("a dead page is reported once after 3 failures, not every time", len([b for _, b in sent if "can't reach" in b]) == 3 and True, str(sent))
    PAGE["code"] = 200
    # validation
    for bad, want in (({"kind": "url", "name": "x", "url": "ftp://x"}, "http"), ({"kind": "url", "name": "x", "url": URL, "condition": "weird"}, "condition"), ({"kind": "nope", "name": "x"}, "kind"), ({"kind": "prompt", "name": "x"}, "prompt")):
        try:
            await T.t_watch_create(ctx, bad); check("rejects " + str(bad)[:40], False)
        except T.ToolError as e:
            check("rejects " + str(bad)[:40], want in str(e), str(e))
    # prompt watcher = quiet routine
    r = await T.t_watch_create(ctx, {"kind": "prompt", "name": "Mail digest", "prompt": "Check my unread mail for anything urgent", "every_minutes": 10})
    q = [x for x in routines.all_routines() if x.get("quiet")]
    check("a prompt watcher is a quiet, read-only, same-chat routine (15 min minimum)", q and q[0]["mode"] == "plan" and q[0]["chat"] == "same" and q[0]["schedule"]["minutes"] == 15, str(q))
    lst = await T.t_watch_list(ctx, {})
    check("watch_list shows both kinds", "[url" in lst and "[prompt" in lst and "Mail digest" in lst, lst)
    wid = W.all_watchers()[0]["id"]
    check("watch_remove deletes a url watcher", await T.t_watch_remove(ctx, {"id": wid}) == "watcher removed" and all(x["id"] != wid for x in W.all_watchers()))
    check("watch_remove deletes a quiet watcher", "quiet watcher removed" in await T.t_watch_remove(ctx, {"id": q[0]["id"]}) and not [x for x in routines.all_routines() if x.get("quiet")])
    try:
        await T.t_watch_remove(ctx, {"id": "nope"}); check("unknown id refused", False)
    except T.ToolError: check("unknown id refused", True)
    # quiet routine says nothing for NOTHING
    class Run: 
        def __init__(s): s.events = []; s.task = asyncio.sleep(0)
    rr = routines.clean({"name": "Q", "prompt": "p", "quiet": True, "schedule": {"kind": "every", "minutes": 15}})
    await routines.save([rr])
    for answer, expect in (("NOTHING", 0), ("nothing new.", 0), ("Your parcel arrived", 1)):
        sent.clear()
        s = {"id": "c1", "messages": [{"role": "assistant", "content": answer}]}
        await routines._watch(rr["id"], s, Run())
        check(f"quiet routine answering {answer!r} sends {expect} notification(s)", len(sent) == expect, str(sent))
    # approvals
    check("watch_create/remove always ask, even in yolo; denied in plan; read-only list is free", all(perms.decide({"mode": "yolo", "allow": []}, t, {})[0] == "ask" for t in ("watch_create", "watch_remove")) and perms.decide({"mode": "plan", "allow": []}, "watch_create", {})[0] == "deny" and perms.decide({"mode": "ask", "allow": []}, "watch_list", {})[0] == "allow")
    check("never 'always allow'", perms.always_rule("watch_create", {}) is None)
asyncio.run(main())
print("\nALL PASSED" if not fails else "\nFAILED: " + str(fails)); sys.exit(1 if fails else 0)
