"""Every hard task's checker must FAIL on the untouched starting files and PASS on the reference solution; otherwise a score would mean nothing."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("APP_PASSWORD", "x"); os.environ.setdefault("DATA_DIR", "/tmp/agent-hard")
from app.evals_hard import HARD_TASKS

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


def seed(files):
    root = Path(tempfile.mkdtemp(prefix="hard-"))
    for name, body in files.items():
        f = root / name; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(body)
    return root


check("twelve tasks with unique names, prompts, checks and generous timeouts", len(HARD_TASKS) == 12 and len({t["name"] for t in HARD_TASKS}) == 12 and all(t["prompt"] and callable(t["check"]) and t["timeout"] >= 300 for t in HARD_TASKS))
for t in HARD_TASKS:
    root = seed(t["files"])
    ok0, note0 = t["check"](root, "")
    check(f"{t['name']}: fails before any work", not ok0, note0)
    for name, body in t["solution"].items():
        f = root / name; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(body)
    if t["name"] == "C++ prime counter":
        r = subprocess.run(["g++", "-O2", "-o", "primes", "primes.cpp"], cwd=root, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    ok1, note1 = t["check"](root, t.get("final", ""))
    check(f"{t['name']}: passes with the reference solution", ok1, note1)
    if "test" in "".join(t["files"]) and not t["name"].startswith("Rename"):          # tests the agent must not edit: tampering is caught
        tf = next(n for n in t["files"] if "test" in n and n.endswith((".py", ".js")))
        (root / tf).write_text((root / tf).read_text() + "\n# tampered\n")
        ok2, note2 = t["check"](root, t.get("final", ""))
        check(f"{t['name']}: editing the test file is caught", not ok2, note2)
    shutil.rmtree(root, ignore_errors=True)
print("ALL PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
