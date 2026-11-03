"""Turso storage against a fake libSQL-over-HTTP server (sqlite3 behind /v2/pipeline), plus eval resume after a restart."""
import asyncio, json, os, shutil, sqlite3, sys, threading, time
shutil.rmtree("/tmp/agent-turso", ignore_errors=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.update({"APP_PASSWORD": "pw", "DATA_DIR": "/tmp/agent-turso", "TURSO_URL": "libsql://fake-db.turso.io", "TURSO_TOKEN": "tok-secret"})
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
conn = sqlite3.connect(":memory:", check_same_thread=False); AUTH = []; FAIL = {"on": False}
fake = FastAPI()


@fake.post("/v2/pipeline")
async def pipe(request: Request):
    AUTH.append(request.headers.get("authorization"))
    if FAIL["on"]:
        return JSONResponse({"error": "down"}, status_code=503)
    out = []
    for rq in (await request.json())["requests"]:
        if rq["type"] == "close":
            out.append({"type": "ok", "response": {"type": "close"}}); continue
        st = rq["stmt"]
        args = [None if a["type"] == "null" else (int(a["value"]) if a["type"] == "integer" else a["value"]) for a in st.get("args", [])]
        try:
            cur = conn.execute(st["sql"], args); conn.commit()
        except Exception as e:
            out.append({"type": "error", "error": {"message": str(e)}}); continue
        cols = [{"name": d[0]} for d in (cur.description or [])]
        rows = [[({"type": "null"} if v is None else {"type": "integer", "value": str(v)} if isinstance(v, int) else {"type": "float", "value": v} if isinstance(v, float) else {"type": "text", "value": v}) for v in r] for r in cur.fetchall()]
        out.append({"type": "ok", "response": {"type": "execute", "result": {"cols": cols, "rows": rows}}})
    return {"results": out}
threading.Thread(target=lambda: uvicorn.run(fake, port=9195, log_level="warning"), daemon=True).start()
os.environ["TURSO_URL"] = "http://127.0.0.1:9195"; time.sleep(1.5)
from app import db, kv, evals, store, config

fails = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: fails.append(n)

async def main():
    check("turso is detected from the env vars", db.enabled() and db.URL == "http://127.0.0.1:9195")
    await kv.put("routines", [{"id": "a", "name": "Morning"}])
    rows = (await db.run([("SELECT name, value FROM kv", [])]))[0]
    check("kv writes land in Turso as JSON", rows and rows[0]["name"] == "routines" and json.loads(rows[0]["value"])[0]["id"] == "a", str(rows))
    check("the token is sent as a bearer header and never stored", AUTH[-1] == "Bearer tok-secret" and "tok-secret" not in json.dumps([r for r in (await db.run([("SELECT * FROM kv", [])]))[0]]))
    await kv.put("routines", [{"id": "b"}])
    check("a second write replaces the first (upsert)", json.loads((await db.run([("SELECT value FROM kv WHERE name = ?", ["routines"])]))[0][0]["value"]) == [{"id": "b"}])
    # a fresh container: local disk is empty, Turso still has it
    kv._cache.clear(); shutil.rmtree(kv.DIR, ignore_errors=True)
    await kv.init()
    check("after a redeploy (empty disk) state comes back from Turso", kv.get("routines") == [{"id": "b"}], str(kv.get("routines")))
    t = asyncio.create_task(db.keepalive_loop(every=0.2, first=0)); await asyncio.sleep(1.5); t.cancel()
    ka = (await db.run([("SELECT updated FROM kv WHERE name = '_keepalive'", [])]))[0]
    check("a daily keepalive write keeps the free database from being archived", ka and time.time() - ka[0]["updated"] < 5, str(ka))
    FAIL["on"] = True
    await kv.put("skills", {"x": 1})
    check("Turso being down never breaks a write (local copy kept)", kv.get("skills") == {"x": 1} and "turso" in (store.remote_status.get("error") or "").lower(), str(store.remote_status))
    FAIL["on"] = False
    # eval resume
    tasks = evals.tasks_for("standard")
    names = [t["name"] for t in tasks]
    done = [{"name": n, "passed": True, "note": "", "steps": 2, "seconds": 1.0} for n in names[:3]]
    await kv.put("eval_active", {"id": "ev1", "model": "m", "effort": "high", "suite": "standard", "review": False, "skipped": [], "status": "running",
                                 "total": len(names), "results": done, "tasks": names, "started": time.time() - 100})
    ran = []
    async def fake_run(eid, model): ran.append((eid, model, len(evals.EVALS[eid]["results"]), len(evals.EVALS[eid]["_tasks"])))
    real = evals.run_eval; evals.run_eval = fake_run
    rid = evals.resume(); await asyncio.sleep(0.1)
    check("an eval that was running when the server died is resumed", rid == "ev1" and ran == [("ev1", "m", 3, 7)] and evals.EVALS["ev1"]["resumed"] == 1, str(ran))
    check("resuming twice in one boot does nothing", evals.resume() is None)
    evals.EVALS.clear()
    await kv.put("eval_active", {"id": "ev2", "model": "m", "status": "running", "tasks": names, "results": [], "resumed": 3, "suite": "standard"})
    check("a run that keeps dying is not resumed forever", evals.resume() is None)
    await kv.put("eval_active", {"id": "ev3", "model": "m", "status": "done", "tasks": names, "results": done, "suite": "standard"})
    check("finished runs are not resumed", evals.resume() is None)
    evals.run_eval = real
asyncio.run(main())
print("\nALL PASSED" if not fails else "\nFAILED: " + str(fails)); sys.exit(1 if fails else 0)
