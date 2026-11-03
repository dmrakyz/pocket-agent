"""Platform check: runs against a real backend with the mock model; every row must report a sensible status and leave no trace."""
import json, os, shutil, subprocess, sys, time
import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = "/tmp/agent-selftest"
shutil.rmtree(DATA, ignore_errors=True)
APP_PORT, LLM_PORT = 9170, 9171
env = os.environ | {"APP_PASSWORD": "pw123", "DATA_DIR": DATA, "ALLOWED_ORIGINS": "*",
                    "PROVIDERS_JSON": json.dumps({"mock": {"base_url": f"http://127.0.0.1:{LLM_PORT}/v1", "api_key": "k"}})}
procs = [subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_llm:app", "--port", str(LLM_PORT), "--log-level", "warning"], cwd=os.path.join(ROOT, "tests"), env=env),
         subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(APP_PORT), "--log-level", "warning"], cwd=os.path.join(ROOT, "backend"), env=env)]
B = f"http://127.0.0.1:{APP_PORT}"
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else ""))
    if not cond: fails.append(name)


try:
    for _ in range(80):
        try:
            if httpx.get(B + "/health", timeout=1).status_code == 200: break
        except Exception: time.sleep(0.25)
    c = httpx.Client(base_url=B, timeout=120)
    check("needs login", c.post("/api/selftest").status_code == 401)
    tok = c.post("/api/login", json={"password": "pw123"}).json()["token"]; A = {"Authorization": "Bearer " + tok}
    r = c.post("/api/selftest", headers=A).json()
    by = {x["name"]: x for x in r["results"]}
    check("every row has a status and a detail", all(x["status"] in ("ok", "warn", "fail", "skip") and x["detail"] for x in r["results"]), str(r["results"])[:300])
    check("summary adds up", sum(r["summary"].values()) == len(r["results"]))
    for n in ("State saving", "Default model", "Memory use", "App drawer", "Tool forging", "Agent as MCP server", "World engine", "Routines", "Connectors", "Push notifications", "Browser"):
        check(f"row exists: {n}", n in by)
    check("model row passes with the mock model", by["Default model"]["status"] == "ok", str(by["Default model"]))
    check("app drawer, forging, MCP server, worlds, routines pass", all(by[n]["status"] == "ok" for n in ("App drawer", "Tool forging", "Agent as MCP server", "World engine", "Routines")), str({n: by[n]["status"] for n in by}))
    check("no state repo shows as a warning, not a pass", by["State saving"]["status"] == "warn" and "GITHUB_TOKEN" in by["State saving"]["detail"])
    check("unset optional features are 'skip', not failures", all(by[n]["status"] == "skip" for n in ("send_email", "Telegram", "Instagram DMs", "vision model")))
    check("the check left no apps behind", c.get("/api/apps", headers=A).json()["apps"] == [])
    check("and no leftover kv junk", not any(f.endswith("__selftest__.json") and json.load(open(os.path.join(DATA, "kv", f))) for f in (os.listdir(os.path.join(DATA, "kv")) if os.path.isdir(os.path.join(DATA, "kv")) else [])))
finally:
    for p in procs: p.terminate()
print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
