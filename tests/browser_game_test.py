"""The browser tool can play: hold keys together, relative mouse look under pointer lock, drag, and the idle timeout is long."""
import asyncio, json, os, sys, threading, http.server
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.update({"APP_PASSWORD": "pw", "DATA_DIR": "/tmp/agent-bg", "BROWSER_ALLOW_LOCAL": "1"})
PAGE = b"""<!doctype html><title>game</title><body style="margin:0"><canvas id=c width=600 height=400 style="background:#234"></canvas>
<script>
window.S={keys:{},held:{},lookX:0,lookY:0,locked:false,drag:null,clicks:0};
const t0={};
addEventListener('keydown',e=>{if(!S.keys[e.key]){S.keys[e.key]=performance.now();}});
addEventListener('keyup',e=>{S.held[e.key]=Math.round(performance.now()-S.keys[e.key]);delete S.keys[e.key];});
c.addEventListener('click',()=>{S.clicks++;c.requestPointerLock();});
document.addEventListener('pointerlockchange',()=>S.locked=document.pointerLockElement===c);
addEventListener('mousemove',e=>{S.lookX+=e.movementX;S.lookY+=e.movementY;});
c.addEventListener('mousedown',e=>S.drag={x:e.offsetX,y:e.offsetY,b:e.button});
addEventListener('mouseup',e=>{if(S.drag){S.drag.ex=e.clientX;S.drag.ey=e.clientY;}});
</script></body>"""
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(PAGE)
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 9341), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
from pathlib import Path
from app import tools as T, browser as B
fails = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: fails.append(n)

async def main():
    ctx = T.Ctx({"id": "g1", "mode": "yolo"}, Path("/tmp"), None)
    r = await T.t_browser_read(ctx, {"action": "open", "url": "http://127.0.0.1:9341/"})
    S = lambda: B.browser.evaluate("g1", "JSON.stringify(window.S)")
    await T.t_browser_act(ctx, {"action": "click", "x": 300, "y": 200})
    st = json.loads(await S())
    check("click on the canvas grabs the pointer", st["locked"] and st["clicks"] == 1, str(st))
    r = await T.t_browser_act(ctx, {"action": "hold", "keys": ["w", "Shift"], "ms": 600})
    st = json.loads(await S())
    check("hold keeps two keys down together for the requested time", 500 <= st["held"].get("w", 0) <= 1400 and 500 <= st["held"].get("Shift", 0) <= 1400 and "held w+Shift" in r["text"], str(st))
    check("keys are released afterwards", not st["keys"])
    await T.t_browser_act(ctx, {"action": "mouse", "kind": "look", "dx": 240, "dy": -80})
    st = json.loads(await S())
    check("mouse look produces relative movement under pointer lock", st["lookX"] > 150 and st["lookY"] < -40, str(st))
    await T.t_browser_act(ctx, {"action": "mouse", "kind": "look", "dx": -240, "dy": 80})
    st2 = json.loads(await S())
    check("looking back reverses it", st2["lookX"] < st["lookX"], str(st2))
    await T.t_browser_act(ctx, {"action": "eval", "js": "document.exitPointerLock(); 1"})
    await asyncio.sleep(0.4)
    await T.t_browser_act(ctx, {"action": "mouse", "kind": "drag", "x": 100, "y": 100, "x2": 300, "y2": 250})
    st = json.loads(await S())
    check("drag presses at the start and releases at the end", st["drag"] and abs(st["drag"]["x"] - 100) < 3 and abs(st["drag"]["ex"] - 300) < 3 and abs(st["drag"]["ey"] - 250) < 3, str(st.get("drag")))
    try:
        await T.t_browser_act(ctx, {"action": "hold", "keys": [], "ms": 100}); bad = False
    except Exception: bad = True
    r = await T.t_browser_act(ctx, {"action": "hold", "keys": [], "ms": 100})
    check("hold without keys explains itself", r.get("error") and "needs keys" in r["text"], str(r)[:120])
    check("browser idles out after 20 minutes now", B.IDLE_SECONDS == 1200)
    names = [t["function"]["name"] for t in T.schemas({}, None)]
    sch = next(t for t in T.schemas({}, None) if t["function"]["name"] == "browser_act")
    check("schema offers hold and mouse", {"hold", "mouse"} <= set(sch["function"]["parameters"]["properties"]["action"]["enum"]))
    await B.browser.shutdown()
asyncio.run(main())
print("\nALL PASSED" if not fails else "\nFAILED: " + str(fails)); sys.exit(1 if fails else 0)
