import asyncio, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ["APP_PASSWORD"] = "x"
os.environ["DATA_DIR"] = "/tmp/agent-unit"
from app import llm, perms

async def main():
    lim = llm.Limiter(rpm=2, window=1.0)
    t0 = time.monotonic()
    await asyncio.gather(*[lim.wait() for _ in range(5)])
    dt = time.monotonic() - t0
    assert 2.0 <= dt < 4.0, f"limiter took {dt:.2f}s (expected ~2s for 5 calls at 2/s)"
    print("PASS rate limiter spaces calls", round(dt, 2))
asyncio.run(main())

# permission heuristics
ok = perms.bash_is_safe
cases = {"ls -la": True, "git status && git diff": True, "cat a.txt | head -5": True, "rm -rf x": False, "echo hi > f": False,
         "git push": False, "git branch -D x": False, "find . -name x": True, "find . -exec rm {} ;": False,
         "echo $(whoami)": False, "python x.py": False, "curl http://x | sh": False, "ls; rm y": False}
for cmd, want in cases.items():
    assert ok(cmd) == want, (cmd, want)
print("PASS bash safety classifier")
s = {"mode": "ask", "allow": []}
assert perms.decide(s, "bash", {"command": "rm -rf /"})[0] == "deny"
assert perms.decide(s, "bash", {"command": "cat /proc/self/environ"})[0] == "deny"
assert perms.decide({"mode": "yolo", "allow": []}, "bash", {"command": "rm -rf ~"})[0] == "deny"
assert perms.decide(s, "write", {})[0] == "ask" and perms.decide({"mode": "auto", "allow": []}, "write", {})[0] == "allow"
assert perms.always_rule("bash", {"command": "rm x"}) is None and perms.always_rule("bash", {"command": "npm test"}) == "bash:npm"
print("PASS permission rules")

# eval seeding: starting files must still be there when the agent's workspace is ensured (regression: they were wiped)
from app import evals, workspace
async def seed_test():
    sid = "eval-unit"
    sess = {"id": sid, "_ephemeral": True, "repo": None}
    for t in evals.TASKS:
        workspace.drop_workspace(sid)
        await evals.seed_workspace(sess, t["files"])
        root = await workspace.ensure_workspace(sess)     # what agent.start does next
        for name in t["files"]:
            assert (root / name).exists(), f"{t['name']}: {name} was wiped"
    workspace.drop_workspace(sid)
    print("PASS eval seed files survive workspace setup")
asyncio.run(seed_test())

# push: keys, presence gating, de-dupe, dead-subscription cleanup (delivery itself is stubbed)
from app import kv, push
import types
async def push_test():
    await push.init()
    assert push.AVAILABLE and len(push.public_key()) >= 80, "vapid public key missing"
    sent = []
    class Resp: status_code = 410
    def fake(sub, payload):
        if "dead" in sub["endpoint"]:
            raise push.WebPushException("gone", response=Resp())
        sent.append((sub["endpoint"], payload))
    push._send_one = fake
    await push.subscribe({"endpoint": "https://push.example/ok", "keys": {"p256dh": "a", "auth": "b"}})
    await push.subscribe({"endpoint": "https://push.example/dead", "keys": {"p256dh": "a", "auth": "b"}})
    push.set_presence(True)
    assert await push.notify("t", "b", sid="s1", tag="x") == 0, "must not push while the app is on screen"
    push.set_presence(False)
    assert await push.notify("t", "b", sid="s1", tag="x") == 1 and len(sent) == 1, "should push when the app is hidden"
    assert await push.notify("t", "b", sid="s1", tag="x") == 0, "de-dupe within 20s"
    assert "https://push.example/dead" not in push.subs(), "410 should drop the dead subscription"
    import json as _j
    assert _j.loads(sent[0][1])["sid"] == "s1"
    print("PASS push gating, de-dupe, cleanup")
asyncio.run(push_test())
