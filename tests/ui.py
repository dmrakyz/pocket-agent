"""Drive the real phone UI in a headless mobile browser against the real backend + mock model, and screenshot it."""
import json
import os
import shutil
import subprocess
import sys
import time

import httpx
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = "/tmp/agent-ui"
OUT = os.environ.get("SHOTS", "/tmp/claude-0/-home-claude/500e6e7d-e4b6-5940-87a0-6ed599f1d1ec/scratchpad/shots")
os.makedirs(OUT, exist_ok=True)
shutil.rmtree(DATA, ignore_errors=True)
env = os.environ | {"APP_PASSWORD": "pw123", "DATA_DIR": DATA, "ALLOWED_ORIGINS": "*",
                    "PROVIDERS_JSON": json.dumps({"mock": {"base_url": "http://127.0.0.1:9201/v1", "api_key": "k"}})}
procs = [
    subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_llm:app", "--port", "9201", "--log-level", "warning"], cwd=f"{ROOT}/tests", env=env),
    subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", "9200", "--log-level", "warning"], cwd=f"{ROOT}/backend", env=env),
    subprocess.Popen([sys.executable, "-m", "http.server", "9202", "--bind", "127.0.0.1"], cwd=f"{ROOT}/frontend", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
]
fails, errors = [], []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


try:
    for _ in range(80):
        try:
            if httpx.get("http://127.0.0.1:9200/health", timeout=1).status_code == 200 and httpx.get("http://127.0.0.1:9202/", timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.25)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        p = ctx.new_page()
        p.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        p.on("pageerror", lambda e: errors.append(str(e)))
        p.goto("http://127.0.0.1:9202/")
        p.wait_for_selector("#setup:not([hidden])")
        p.screenshot(path=f"{OUT}/1-setup.png")
        p.fill("#in-url", "http://127.0.0.1:9200")
        p.fill("#in-pw", "wrong")
        p.click("#btn-connect")
        p.wait_for_function("document.querySelector('#setup-status').textContent.includes('wrong password')", timeout=8000)
        check("wrong password shows error", True)
        p.fill("#in-pw", "pw123")
        p.click("#btn-connect")
        p.wait_for_selector("#main:not([hidden])")
        p.wait_for_selector("#empty:not([hidden])")
        p.screenshot(path=f"{OUT}/2-empty.png")
        check("signed in, empty state", True)

        p.click("#btn-empty-new")
        p.wait_for_selector("#sheet.open")
        p.screenshot(path=f"{OUT}/3-newchat.png")
        p.click("text=Start chat")
        p.wait_for_function("window.__agent.cur !== null")
        check("chat created", p.evaluate("window.__agent.cur.mode") == "ask")

        p.fill("#input", "MULTI please build it")
        p.click("#btn-send")
        approved = 0
        t0 = time.time()
        shot_approval = False
        while time.time() - t0 < 60:
            if p.evaluate("window.__agent.finished === true && window.__agent.run.running === false") and approved:
                break
            btn = p.query_selector(".approve .btn-allow")
            if btn:
                if not shot_approval:
                    p.screenshot(path=f"{OUT}/4-approval.png")
                    shot_approval = True
                txt = p.inner_text(".approve .q")
                (p.query_selector(".approve .btn-always") if "write" in txt.lower() else btn).click()
                approved += 1
                time.sleep(0.3)
            else:
                time.sleep(0.2)
        time.sleep(1.0)
        check("agent run finished via UI", p.evaluate("window.__agent.run.running") is False)
        check("approval UI was used", approved >= 2, str(approved))
        cards = p.query_selector_all(".tool")
        check("tool cards rendered", len(cards) >= 8, str(len(cards)))
        check("page did not scroll under the header", p.evaluate("document.querySelector('#app').scrollTop") == 0 and p.is_visible("header"))
        p.screenshot(path=f"{OUT}/5-done.png")
        # open the edit card to see the diff
        edit_head = p.query_selector(".tool:has(.tool-name:text-is('edit')) .tool-head")
        if edit_head:
            edit_head.click()
            p.wait_for_selector(".diff .add")
            check("diff rendered with additions", True)
            p.screenshot(path=f"{OUT}/6-diff.png")
        p.click("#btn-menu")
        p.wait_for_selector("#drawer.open")
        p.screenshot(path=f"{OUT}/7-drawer.png")
        check("chat list shows the chat", len(p.query_selector_all(".chat-item")) >= 1)
        p.click("#scrim", position={"x": 380, "y": 400})
        p.click("#chip-mode")
        p.wait_for_selector("#sheet.open")
        p.screenshot(path=f"{OUT}/8-mode.png")
        p.click("text=Plan")
        p.wait_for_function("window.__agent.cur.mode === 'plan'")
        check("mode switch", True)
        p.click("#chip-model")
        p.wait_for_selector(".opt")
        p.screenshot(path=f"{OUT}/9-model.png")
        n_all = len(p.query_selector_all("#sheet-body .opt"))
        p.fill(".msearch", "zzz-nothing")
        check("model search filters to none", len(p.query_selector_all("#sheet-body .opt")) == 0)
        p.fill(".msearch", "")
        check("model search clears", len(p.query_selector_all("#sheet-body .opt")) == n_all)
        p.click(".seg button:has-text('Medium')")
        p.wait_for_function("window.__agent.cur.effort === 'medium'")
        check("effort chip shows Medium", "Medium" in p.inner_text("#chip-model"))
        check("Medium segment highlighted", p.evaluate("document.querySelector('.seg button.on').textContent") == "Medium")
        p.screenshot(path=f"{OUT}/9b-model-effort.png")
        p.click("#sheet-close")
        p.click("#btn-more")
        p.wait_for_selector("text=Browse files")
        p.screenshot(path=f"{OUT}/10-menu.png")
        p.click("text=Browse files")
        p.wait_for_selector(".fileitem")
        check("file browser lists hello.py", "hello.py" in p.inner_text("#sheet-body"))
        p.screenshot(path=f"{OUT}/11-files.png")
        p.click("#sheet-close")
        # sub-agent approval must be visible in the UI (it used to hang invisibly)
        p.click("#chip-mode"); p.wait_for_selector("#sheet.open"); p.click("#sheet-body .opt:has-text('Asks before edits')"); p.wait_for_function("window.__agent.cur.mode === 'ask'")
        p.fill("#input", "SUBAPPROVE please"); p.click("#btn-send")
        p.wait_for_selector(".tool.ask[data-standalone] .btn-allow", timeout=30000)
        check("sub-agent approval card shows in the UI", "sub-agent" in p.inner_text(".tool.ask .q"))
        p.screenshot(path=f"{OUT}/13-sub-approval.png")
        p.click(".tool.ask .btn-allow")
        p.wait_for_function("window.__agent.run.running === false", timeout=30000)
        # upload a file through the real file input
        import zipfile as _z
        with _z.ZipFile("/tmp/upl.zip", "w") as _zf:
            _zf.writestr("a/b.txt", "hi")
        p.set_input_files("#file-img", "/tmp/upl.zip")
        p.wait_for_selector(".filechip")
        p.wait_for_function("window.__agent.files.length === 1 && window.__agent.files[0].state === 'ready'", timeout=15000)
        check("file upload chip ready", p.evaluate("window.__agent.files[0].extracted") == "uploads/upl")
        p.screenshot(path=f"{OUT}/14-upload.png")
        # reload page: state restores from server
        p.reload()
        p.wait_for_selector(".tool")
        check("reload restores history", len(p.query_selector_all(".tool")) >= 8)
        # desktop width layout
        p.set_viewport_size({"width": 1100, "height": 800})
        p.screenshot(path=f"{OUT}/12-desktop.png")
        b.close()
    real = [e for e in errors if "401" not in e]   # the deliberate wrong-password attempt logs one 401
    check("no console/page errors", not real and len(errors) <= 1, str(errors[:5]))
finally:
    for pr in procs:
        pr.terminate()
print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
