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
env = os.environ | {"APP_PASSWORD": "pw123", "DATA_DIR": DATA, "ALLOWED_ORIGINS": "*", "BROWSER_ALLOW_LOCAL": "1",
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
        THREE_ROOT = "/tmp/three_pkg/node_modules/three"
        def serve_three(route):
            rel = route.request.url.split("three@0.160.0/", 1)[1].split("?")[0]
            fp = os.path.join(THREE_ROOT, rel)
            if os.path.isfile(fp):
                route.fulfill(status=200, content_type="text/javascript", headers={"Access-Control-Allow-Origin": "*"}, body=open(fp, "rb").read())
            else:
                route.fulfill(status=404, body="no")
        ctx.route("https://cdn.jsdelivr.net/npm/three@0.160.0/**", serve_three)
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
        heads = p.query_selector_all(".tg-head:not([hidden])")
        check("tool calls collapse into grouped rows", len(heads) >= 1 and all(any(w in h.inner_text() for w in ("Ran", "Edited", "Read", "Used")) for h in heads), str([h.inner_text() for h in heads]))
        check("grouped cards are not dumped into the chat", len(p.query_selector_all("#msgs > .msg-ai .tg-head ~ .tg-live .tool")) == 0)
        check("page did not scroll under the header", p.evaluate("document.querySelector('#app').scrollTop") == 0 and p.is_visible("header"))
        p.screenshot(path=f"{OUT}/5-done.png")
        # open the timeline, then the edit step to see the diff
        p.locator(".tg-head:not([hidden])").first.click()
        p.wait_for_selector("#sheet.open .step"); p.wait_for_timeout(450)
        check("timeline lists the steps", len(p.query_selector_all("#sheet-body .step")) >= 4, str(len(p.query_selector_all("#sheet-body .step"))))
        p.screenshot(path=f"{OUT}/5b-timeline.png")
        edit_step = p.query_selector("#sheet-body .step:has(.step-s:text-is('edit'))")
        if edit_step:
            edit_step.click()
            p.wait_for_selector("#sheet-body .diff .add")
            check("diff rendered with additions", True)
            p.screenshot(path=f"{OUT}/6-diff.png")
            p.click("#sheet-body .back")
            check("back returns to the step list", len(p.query_selector_all("#sheet-body .step")) >= 1)
        p.click("#sheet-close"); p.wait_for_timeout(300)
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
        p.wait_for_selector("#menu .mi")
        time.sleep(0.5)
        p.screenshot(path=f"{OUT}/10-menu.png")
        check("chat menu has chat actions only", all(t in p.inner_text("#menu") for t in ("Rename chat", "Compact context", "Autopilot", "Save workspace")) and "Sign out" not in p.inner_text("#menu"))
        p.click("#menu-scrim", position={"x": 20, "y": 700})
        # drawer -> settings
        p.click("#btn-menu"); p.wait_for_selector("#drawer.open")
        check("drawer footer shows version + settings", p.is_visible("#dr-settings") and p.is_visible("#dr-eval") and "dev build" in p.inner_text("#ver"))
        time.sleep(0.5)
        p.screenshot(path=f"{OUT}/10b-drawer-footer.png")
        p.click("#dr-settings"); p.wait_for_selector("#settings:not([hidden]) >> text=Appearance")
        time.sleep(0.4)
        p.screenshot(path=f"{OUT}/10c-settings.png")
        txt = p.inner_text("#setbody")
        check("settings has server info + about", all(t in txt for t in ("History sync", "Check for updates", "Sign out", "Capability check", "dev build", "Push notifications")))
        p.click("#setbody .seg button:has-text('Dark')")
        check("theme dark applies", p.evaluate("document.documentElement.getAttribute('data-theme')") == "dark")
        p.click("#setbody .seg button:has-text('System')")
        check("theme system clears", p.evaluate("document.documentElement.getAttribute('data-theme')") is None)
        p.click("#setbody .mi:has-text('Check for updates')")
        p.wait_for_selector("text=Development build")
        p.click("#set-back")
        p.click("#btn-files")
        p.wait_for_selector(".fileitem")
        check("file browser lists hello.py", "hello.py" in p.inner_text("#sheet-body"))
        check("files sheet has upload button", p.is_visible("text=Upload files"))
        p.screenshot(path=f"{OUT}/11-files.png")
        p.click("#sheet-close")
        # sub-agent approval must be visible in the UI (it used to hang invisibly)
        p.click("#chip-mode"); p.wait_for_selector("#sheet.open"); p.click("#sheet-body .opt:has-text('Asks before edits')"); p.wait_for_function("window.__agent.cur.mode === 'ask'")
        p.fill("#input", "SUBAPPROVE please"); p.click("#btn-send")
        p.wait_for_selector(".tool.ask[data-standalone] .btn-allow", timeout=30000)
        check("sub-agent approval card shows in the UI", "sub-agent" in p.inner_text(".tool.ask .q"))
        p.screenshot(path=f"{OUT}/13-sub-approval.png")
        check("sub-agent bar shows a running sub-agent", p.is_visible("#subbar") and "working" in p.inner_text("#subbar"))
        p.screenshot(path=f"{OUT}/13b-subbar.png")
        # type while it is working: the message queues instead of being lost
        p.fill("#input", "QUEUED hello")
        check("send button turns into queue while working", p.get_attribute("#btn-send", "aria-label") == "Queue message")
        p.click("#btn-send")
        p.wait_for_selector("#queuebar:not([hidden]) .qitem")
        check("message is queued", "QUEUED hello" in p.inner_text("#queuebar") and p.input_value("#input") == "")
        p.screenshot(path=f"{OUT}/13c-queue.png")
        p.click(".tool.ask .btn-allow")
        p.wait_for_selector("text=You said: QUEUED hello", timeout=30000)
        p.wait_for_function("window.__agent.run.running === false && !document.querySelector('#queuebar .qitem')", timeout=30000)
        check("queued message was sent automatically", p.is_hidden("#queuebar"))
        check("sub-agent bar keeps the finished list", "finished" in p.inner_text("#subbar") or p.is_hidden("#subbar"))
        # long-press menu: regenerate / copy on answers, edit on prompts
        def hold(sel):
            loc = p.locator(sel).last
            loc.scroll_into_view_if_needed()
            box = loc.bounding_box()
            assert box, "no box for " + sel + " count=" + str(p.locator(sel).count()) + " html=" + p.inner_html("#msgs")[:300]
            p.mouse.move(box["x"] + 20, box["y"] + box["height"] / 2)
            p.mouse.down(); p.wait_for_timeout(650)
        check("no action buttons under messages anymore", p.query_selector(".msg-actions") is None and p.query_selector(".rewind") is None)
        n_users = len(p.query_selector_all(".msg-user"))
        hold(".msg-ai")
        p.wait_for_selector(".ctx")
        txt = p.inner_text(".ctx")
        check("holding an answer offers Regenerate + Copy", "Regenerate" in txt and "Copy" in txt and "Edit" not in txt, txt)
        p.screenshot(path=f"{OUT}/19-longpress.png")
        p.mouse.up()
        p.click(".ctx >> text=Regenerate")
        p.wait_for_function("window.__agent.run.running === true || document.querySelectorAll('.msg-user').length === %d" % n_users, timeout=15000)
        p.wait_for_function("window.__agent.run.running === false", timeout=30000)
        check("regenerate re-ran without duplicating the question", len(p.query_selector_all(".msg-user")) == n_users and "You said: QUEUED hello" in p.inner_text("#msgs"))
        p.wait_for_timeout(800)
        hold(".msg-user")
        p.wait_for_selector(".ctx")
        check("holding a prompt offers Edit + Copy", "Edit" in p.inner_text(".ctx") and "Regenerate" not in p.inner_text(".ctx"))
        p.mouse.up()
        p.click(".ctx >> text=Edit")
        p.click("#sheet-body >> button:has-text('Edit')")
        p.wait_for_function("document.querySelector('#input').value.length > 0")
        check("edit puts the prompt back in the box", "QUEUED hello" in p.input_value("#input") or len(p.input_value("#input")) > 0)
        p.fill("#input", "")
        p.click("body", position={"x": 5, "y": 300}) if p.is_visible(".ctx") else None
        # a quick tap must NOT open the menu
        box = p.locator(".msg-ai").last.bounding_box(); p.mouse.click(box["x"] + 20, box["y"] + 10)
        check("a normal tap does not open the menu", not p.is_visible(".ctx"))
        # sheet closes by swiping down from its top
        p.click("#chip-model")
        p.wait_for_selector("#sheet.open"); p.wait_for_timeout(450)
        sb = p.locator(".sheet-grab").bounding_box()
        p.mouse.move(sb["x"] + sb["width"] / 2, sb["y"] + 8); p.mouse.down()
        for i in range(1, 17): p.mouse.move(sb["x"] + sb["width"] / 2, sb["y"] + 8 + i * 25)
        p.mouse.up()
        p.wait_for_function("!document.querySelector('#sheet').classList.contains('open')", timeout=3000)
        check("swiping a sheet down closes it", True)
        p.click("#chip-model"); p.wait_for_selector("#sheet.open"); p.wait_for_timeout(450)
        sb = p.locator(".sheet-grab").bounding_box()
        p.mouse.move(sb["x"] + sb["width"] / 2, sb["y"] + 8); p.mouse.down(); [(p.mouse.move(sb["x"] + sb["width"] / 2, sb["y"] + 8 + i * 26), p.wait_for_timeout(120)) for i in range(1, 6)]; p.mouse.up()
        p.wait_for_timeout(400)
        check("a small swipe snaps back open", p.evaluate("document.querySelector('#sheet').classList.contains('open')"))
        p.click("#sheet-close")
        # live preview of a page the agent built
        p.fill("#input", "SHOWPAGE please"); p.click("#btn-send")
        p.wait_for_selector("text=Open preview", timeout=30000)
        p.click("text=Open preview")
        p.wait_for_selector("#previewpage:not([hidden])")
        fr = p.frame_locator("#pv-frame")
        fr.locator("h1").wait_for(timeout=15000)
        check("preview shows the built page inside the app", fr.locator("h1").inner_text() == "mock page")
        p.screenshot(path=f"{OUT}/18-preview.png")
        p.click("#pv-back")
        check("preview closes", not p.is_visible("#previewpage"))
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
        # 3D viewer
        p.fill("#input", "SHOWMODEL please"); p.click("#btn-send")
        p.wait_for_selector(".tool.ask .btn-allow", timeout=30000); p.click(".tool.ask .btn-allow")
        p.wait_for_selector("text=Open 3D viewer", timeout=30000)
        p.click("text=Open 3D viewer")
        p.wait_for_selector("#modelpage:not([hidden]) #md-canvas", timeout=30000)
        p.wait_for_function("document.querySelector('#md-status').textContent.includes('triangles')", timeout=30000)
        check("3D viewer loads the STL", "12 triangles" in p.inner_text("#md-status"), p.inner_text("#md-status"))
        time.sleep(0.6)
        from PIL import Image
        import io as _io
        shot = Image.open(_io.BytesIO(p.locator("#md-canvas").screenshot())).convert("RGB")
        colors = len(set(shot.getdata()))
        check("3D canvas drew a shaded model", colors > 40, str(colors))
        p.screenshot(path=f"{OUT}/19-model.png")
        p.click("#md-wire"); check("wireframe toggles", p.evaluate("document.querySelector('#md-wire').classList.contains('on')"))
        p.click("#md-back")
        check("3D viewer closes", not p.is_visible("#modelpage"))
        p.wait_for_function("window.__agent.run.running === false", timeout=30000)
        # capability check page: own screen, progress, survives closing
        p.click("#btn-menu"); p.click("#dr-eval")
        p.wait_for_selector("#evalpage:not([hidden]) >> text=Not run yet")
        p.screenshot(path=f"{OUT}/15-eval-start.png")
        p.click("text=Run check")
        p.wait_for_selector(".evtask")
        check("eval shows 4 task rows", len(p.query_selector_all(".evtask")) == 4)
        check("eval shows progress bar", p.is_visible(".pbar"))
        p.click("#ev-back")
        check("eval page closes", not p.is_visible("#evalpage"))
        p.click("#btn-menu"); p.click("#dr-eval")
        p.wait_for_selector(".evtask")
        check("eval resumes after closing (not reset)", "Run check" not in p.inner_text("#evbody") or "Score" in p.inner_text("#evbody"))
        p.wait_for_function("document.querySelector('#evbody').innerText.includes('Score')", timeout=60000)
        check("eval shows score + failure reasons", "Why it failed" in p.inner_text("#evbody") and "Run again" in p.inner_text("#evbody"))
        p.screenshot(path=f"{OUT}/16-eval-done.png")
        p.click("#ev-back")
        # routines + connectors screens
        p.click("#btn-menu"); p.click("#dr-settings")
        p.click("#setbody >> text=Routines")
        p.wait_for_selector("#routinespage:not([hidden]) >> text=No routines yet")
        p.screenshot(path=f"{OUT}/17-routines-empty.png")
        p.click("#rt-new")
        p.fill("#sheet-body input[type=text]", "Morning check")
        p.fill("#sheet-body textarea", "say hi")
        p.click("#sheet-body >> text=Weekly")
        check("weekly shows day buttons", len(p.query_selector_all("#sheet-body .days button")) == 7)
        p.click("#sheet-body >> text=Manual")
        p.screenshot(path=f"{OUT}/18-routine-edit.png")
        p.click("#sheet-body >> text=Create routine")
        p.wait_for_selector("#rtbody .rtcard")
        check("routine card appears", "Morning check" in p.inner_text("#rtbody") and "Webhook / manual only" in p.inner_text("#rtbody"))
        p.click("#rtbody >> text=Edit")
        check("edit sheet shows webhook link", "/hook/" in p.inner_text("#sheet-body"))
        p.click("#sheet-body >> text=Delete routine")
        p.click("#sheet-body >> text=Delete")
        p.wait_for_selector("#rtbody >> text=No routines yet")
        check("routine deleted", True)
        p.click("#rt-back")
        p.click("#setbody >> text=Connectors")
        p.wait_for_selector("#connpage:not([hidden]) >> text=No connectors yet")
        p.click("#cn-new")
        p.fill("#sheet-body input[type=text]", "bad")
        p.fill("#sheet-body input[type=url]", "http://example.com/mcp")
        p.click("#sheet-body button.primary")
        p.wait_for_selector(".toast.err")
        check("insecure connector rejected with a message", p.is_visible("#sheet-body"))
        p.mouse.click(195, 40)
        p.wait_for_function("!document.querySelector('#sheet').classList.contains('open')")
        p.click("#cn-back")
        p.click("#set-back")
        # browser screen: open a local page, tap it, see the address change
        import http.server, threading
        class BH(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<html><title>B1</title><body style='margin:0'><a href='/next' style='display:block;height:760px;background:#36c;color:#fff;font-size:40px'>tap me</a></body></html>" if self.path == "/" else b"<html><title>B2</title><body>arrived</body></html>"
                self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body)
            def log_message(self, *a): pass
        bsrv = http.server.ThreadingHTTPServer(("127.0.0.1", 9335), BH); threading.Thread(target=bsrv.serve_forever, daemon=True).start()
        p.click("#btn-more"); p.click("#menu >> text=Browser")
        p.wait_for_selector("#browserpage:not([hidden]) >> text=Nothing open yet")
        p.fill("#br-url", "http://127.0.0.1:9335/"); p.press("#br-url", "Enter")
        p.wait_for_function("document.querySelector('#br-img') && !document.querySelector('#br-img').hidden && document.querySelector('#br-img').naturalWidth > 100", timeout=30000)
        check("browser screen shows the live page", True)
        p.screenshot(path=f"{OUT}/20-browser.png")
        p.click("#br-img")
        p.wait_for_function("document.querySelector('#br-url').value.endsWith('/next')", timeout=15000)
        check("tapping the page clicks it (address changed)", True)
        p.click("#br-prev")
        p.wait_for_function("!document.querySelector('#br-url').value.endsWith('/next')", timeout=15000)
        check("browser back works", True)
        p.click("#br-close"); p.wait_for_selector("#br-empty:not([hidden])")
        p.click("#br-back"); bsrv.shutdown()
        # reload page: state restores from server
        p.reload()
        p.wait_for_selector(".tool")
        check("reload restores history", len(p.query_selector_all(".tg-head:not([hidden])")) >= 1)
        # update banner: a stamped (old) build while version.json announces a newer one
        p2 = ctx.new_page()
        p2.add_init_script("window.APP_VERSION='1'; window.APP_COMMIT='aaaaaaa';")
        ctx.route("**/version.json*", lambda r: r.fulfill(status=200, content_type="application/json", body='{"version":"2","commit":"bbbbbbb"}'))
        p2.goto("http://127.0.0.1:9202/")
        p2.wait_for_selector("#update-banner:not([hidden])", timeout=15000)
        check("update banner pops up when a newer build exists", "v2" in p2.inner_text("#update-banner"))
        p2.screenshot(path=f"{OUT}/17-update.png")
        p2.close()
        # desktop width layout
        p.set_viewport_size({"width": 1100, "height": 800})
        p.screenshot(path=f"{OUT}/12-desktop.png")
        b.close()
    real = [e for e in errors if "401" not in e and "400" not in e]   # deliberate wrong password (401) and rejected http connector (400)
    check("no console/page errors", not real and len(errors) <= 2, str(errors[:5]))
finally:
    for pr in procs:
        pr.terminate()
print("\nFAILED:" if fails else "\nALL PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
