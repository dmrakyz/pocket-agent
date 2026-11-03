"""Live preview can run a multi-file, multi-language page: html + js + css + .wasm (what emcc produces) + a python source the page reads."""
import json, os, shutil, subprocess, sys, time
import httpx
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = "/tmp/agent-wasm"
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


# add(a, b) as a hand-assembled wasm module, same shape as an emcc export
WASM = bytes([0, 97, 115, 109, 1, 0, 0, 0, 1, 7, 1, 96, 2, 127, 127, 1, 127, 3, 2, 1, 0, 7, 7, 1, 3, 97, 100, 100, 0, 0, 10, 9, 1, 7, 0, 32, 0, 32, 1, 106, 11])
HTML = """<!doctype html><html><body><pre id=out>loading</pre>
<link rel=stylesheet href=style.css><script type=module src=main.js></script></body></html>"""
JS = """import { shout } from './lib/util.js';
const out = document.getElementById('out');
try {
  const { instance } = await WebAssembly.instantiateStreaming(fetch('add.wasm'), {});
  const py = await (await fetch('model.py')).text();
  out.textContent = shout('sum=' + instance.exports.add(40, 2)) + '|py=' + py.split('\\n')[0] + '|ls=' + (() => { try { localStorage.x = 1; return 'ok'; } catch (e) { return 'blocked'; } })();
} catch (e) { out.textContent = 'ERR ' + e; }
"""
try:
    for _ in range(80):
        try:
            if httpx.get(B + "/health", timeout=1).status_code == 200: break
        except Exception: time.sleep(0.25)
    c = httpx.Client(base_url=B, timeout=60)
    A = {"Authorization": "Bearer " + c.post("/api/login", json={"password": "pw123"}).json()["token"]}
    sid = c.post("/api/sessions", headers=A, json={"mode": "yolo"}).json()["id"]
    base = c.post(f"/api/sessions/{sid}/preview-token", headers=A).json()["base"]
    ws = os.path.join(DATA, "ws", sid)
    os.makedirs(os.path.join(ws, "lib"), exist_ok=True)
    for name, data in (("index.html", HTML), ("main.js", JS), ("style.css", "pre{color:red}"), ("lib/util.js", "export const shout = (s) => s.toUpperCase();"), ("model.py", "# python source\nprint(1)\n")):
        open(os.path.join(ws, name), "w").write(data)
    open(os.path.join(ws, "add.wasm"), "wb").write(WASM)
    r = c.get(base + "add.wasm")
    check("wasm is served as application/wasm", r.headers["content-type"].startswith("application/wasm"), r.headers.get("content-type"))
    check("preview sends the sandbox CSP and CORS for sibling fetches", "sandbox" in r.headers["content-security-policy"] and r.headers["access-control-allow-origin"] == "*")
    with sync_playwright() as pw:
        b = pw.chromium.launch(); pg = b.new_page()
        pg.goto(B + base, wait_until="networkidle")
        pg.wait_for_function("document.getElementById('out').textContent !== 'loading'", timeout=15000)
        txt = pg.inner_text("#out")
        b.close()
    check("html + es module + css + wasm + python file all work together in the sandboxed preview", txt.startswith("SUM=42|py=# python source"), txt)
    check("sandbox still blocks page storage (opaque origin)", txt.endswith("ls=blocked"), txt)
finally:
    for p in procs: p.terminate()
print("ALL PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
