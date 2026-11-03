"""Tiny agentic eval: real multi-step tasks, checked by code. Lets you see what a model can actually do."""
import asyncio
import hashlib
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import agent, tools, workspace

EVALS: dict[str, dict] = {}

TEST_STATS = """from stats import median
assert median([3, 1, 2]) == 2
assert median([4, 1, 3, 2]) == 2.5
assert median([5]) == 5
print("ok")
"""
BUGGY_STATS = """def median(xs):
    n = len(xs)
    return xs[n // 2]
"""


def _run(root: Path, *cmd) -> tuple[int, str]:
    p = subprocess.run(list(cmd), cwd=root, capture_output=True, text=True, timeout=30)
    return p.returncode, (p.stdout + p.stderr)


def _check_fizz(root, final):
    rc, out = _run(root, sys.executable, "-c",
                   "from fizzbuzz import fizzbuzz; r=fizzbuzz(15); assert r[2]=='Fizz' and r[4]=='Buzz' "
                   "and r[14]=='FizzBuzz' and r[0]=='1' and len(r)==15; print('ok')")
    return rc == 0, out[-200:]


def _check_bug(root, final):
    ok_hash = hashlib.sha256((root / "test_stats.py").read_bytes()).hexdigest() == hashlib.sha256(
        TEST_STATS.encode()).hexdigest()
    rc, out = _run(root, sys.executable, "test_stats.py")
    return rc == 0 and ok_hash, ("tests were modified" if not ok_hash else out[-200:])


def _check_explore(root, final):
    ok = "billing/tax_rules.py" in final and "0.0825" in final
    return ok, final[-200:]


def _check_pkg(root, final):
    rc, out = _run(root, sys.executable, "-c",
                   "from calc.ops import add, mul, div\nassert add(2,3)==5 and mul(2,3)==6 and div(6,3)==2\n"
                   "try:\n div(1,0)\n raise SystemExit('no error')\nexcept ValueError: print('ok')")
    rc2, out2 = _run(root, sys.executable, "-m", "unittest", "discover", "-s", ".", "-p", "test*.py")
    return rc == 0 and rc2 == 0 and "OK" in out2, (out + out2)[-200:]


def _app_hits():
    from . import apps
    return [a for a in apps.index().values() if "counter" in a["name"].lower()]


def _check_app(root, final):
    from . import apps
    for a in _app_hits():
        html = apps.html_of(a["id"]).lower()
        if "<button" in html and "<script" in html and "http://" not in html and "https://" not in html:
            return True, "app saved with a button and a script, no outside resources"
        return False, "app exists but has no button/script, or loads outside resources"
    return False, "no app named Counter was created"


async def _cleanup_app():
    from . import apps
    for a in _app_hits():          # leave the user's drawer clean
        await apps.delete(a["id"])


TASKS = [
    {"name": "Write + run a function", "files": {},
     "prompt": "Create fizzbuzz.py with a function fizzbuzz(n) that returns a list of strings for 1..n using the "
               "classic Fizz/Buzz/FizzBuzz rules (numbers as strings). Run it for n=15 to verify.",
     "check": _check_fizz},
    {"name": "Debug failing tests", "files": {"stats.py": BUGGY_STATS, "test_stats.py": TEST_STATS},
     "prompt": "Running `python test_stats.py` fails. Fix stats.py (do NOT edit the test) and confirm it passes.",
     "check": _check_bug},
    {"name": "Explore codebase", "files": {
        "billing/__init__.py": "", "billing/invoice.py": "from .tax_rules import compute_tax\n\ndef total(x):\n    return x + compute_tax(x)\n",
        "billing/tax_rules.py": "def compute_tax(amount, rate=0.0825):\n    return amount * rate\n",
        "billing/refund.py": "def refund(x):\n    return -x\n", "README.md": "# billing\n",
        "utils/tax_legacy.py": "def tax(amount):\n    return amount * 0.2\n", "utils/__init__.py": ""},
     "prompt": "Which file defines the function `compute_tax`, and what is its default rate? "
               "End your answer with a final line formatted exactly: FILE=<path> RATE=<value>",
     "check": _check_explore},
    {"name": "Multi-file build + tests", "files": {},
     "prompt": "Create a Python package `calc/` (with __init__.py and ops.py) implementing add(a,b), mul(a,b) and "
               "div(a,b), where div raises ValueError on division by zero. Write unit tests in test_calc.py using "
               "unittest and run them until they pass.",
     "check": _check_pkg},
    {"name": "Build an app", "files": {},
     "prompt": "Use the create_app tool to build a tiny app called Counter: a big number, a + button and a - button, and it must remember the number after the app is closed "
               "(use localStorage). One self-contained HTML page, no external resources. Then say it is ready.",
     "check": _check_app, "cleanup": _cleanup_app},
]


def _last_activity(s: dict) -> str:
    for m in reversed(s["messages"]):
        if m["role"] == "assistant":
            calls = m.get("tool_calls") or []
            if calls:
                f = calls[-1]["function"]
                return f"{f['name']} {f['arguments'][:70]}"
            return "writing a reply"
    return "starting"


async def seed_workspace(s: dict, files: dict):
    """Create the git workspace FIRST, then drop the starting files in. (Seeding before the repo exists
    made ensure_workspace wipe the folder, so the model saw an empty workspace.)"""
    root = await workspace.ensure_workspace(s)
    for name, body in files.items():
        f = root / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    return root


async def run_eval(eval_id: str, model: str):
    ev = EVALS[eval_id]
    for idx, t in enumerate(TASKS):
        if ev.get("cancel"):
            break
        sid = "eval-" + uuid.uuid4().hex[:8]
        s = {"id": sid, "title": t["name"], "model": model, "mode": "yolo", "thinking": True, "repo": None,
             "messages": [], "todos": [], "allow": [], "usage": {"in": 0, "out": 0}, "_ephemeral": True}
        root = await seed_workspace(s, t["files"])
        started = time.time()
        res = {"name": t["name"], "passed": False, "note": "", "steps": 0}
        ev["live"] = {"index": idx, "started": started, "steps": 0, "last": "starting"}
        try:
            run = agent.start(s, t["prompt"])
            ev["_run"] = run
            while not run.task.done():
                if time.time() - started > 300:
                    run.task.cancel()
                    raise asyncio.TimeoutError()
                await asyncio.wait({run.task}, timeout=1)
                ev["live"].update(steps=sum(1 for m in s["messages"] if m["role"] == "assistant"), last=_last_activity(s))
            if run.task.cancelled() or ev.get("cancel"):
                res["note"] = "stopped"
            else:
                final = next((m.get("content") or "" for m in reversed(s["messages"])
                              if m["role"] == "assistant" and m.get("content")), "")
                res["steps"] = sum(1 for m in s["messages"] if m["role"] == "assistant")
                err = next((e["text"] for e in run.events if e["type"] == "error"), "")
                if err:
                    res["note"] = err[:400]
                else:
                    res["passed"], res["note"] = t["check"](root, final)
        except asyncio.TimeoutError:
            res["note"] = "timed out (300s)"
        except Exception as e:  # noqa: BLE001
            res["note"] = f"{type(e).__name__}: {e}"[:400]
        if t.get("cleanup"):
            try:
                await t["cleanup"]()
            except Exception:  # noqa: BLE001
                pass
        res["seconds"] = round(time.time() - started, 1)
        ev["results"].append(res)
        workspace.drop_workspace(sid)
    ev["live"] = None
    ev["_run"] = None
    ev["status"] = "stopped" if ev.get("cancel") else "done"


def public(ev: dict) -> dict:
    out = {k: v for k, v in ev.items() if not k.startswith("_")}
    out["now"] = time.time()
    return out


def stop_eval(eid: str):
    ev = EVALS[eid]
    ev["cancel"] = True
    run = ev.get("_run")
    if run and run.task and not run.task.done():
        run.task.cancel()


def start_eval(model: str) -> str:
    eid = uuid.uuid4().hex[:8]
    EVALS[eid] = {"id": eid, "model": model, "status": "running", "total": len(TASKS), "results": [],
                  "tasks": [t["name"] for t in TASKS], "started": time.time(), "live": None}
    asyncio.create_task(run_eval(eid, model))
    return eid
