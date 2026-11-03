"""Fake OpenAI-compatible server that plays scripted tool-calling scenarios. For testing the platform offline."""
import json
import re
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
SEEN = {"thinking_param": [], "models": []}
TOOLS_OFFERED = [False]


@app.get("/v1/models")
async def models():
    return {"data": [{"id": "mock/ultra-550b"}, {"id": "mock/lightning-30b"}]}


def sse(delta, finish=None):
    return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"


def tool_call(name, args, raw=None):
    return {"name": name, "args": raw if raw is not None else json.dumps(args)}


def task(desc, prompt, model=None, can_edit=False):
    a = {"description": desc, "prompt": prompt}
    if model:
        a["model"] = model
    if can_edit:
        a["can_edit"] = True
    return tool_call("task", a)


def scenario(msgs, model):
    """Return ('text', str) or ('tools', [call]) or ('inline', str) based on the conversation so far."""
    sysmsg = msgs[0]["content"] if msgs and msgs[0]["role"] == "system" else ""
    if "Summarize this agent conversation" in sysmsg:
        return "text", "SUMMARY-OK: user built hello.py"
    if "title for this chat" in sysmsg:
        return "text", "Mock Chat Title"

    def text_of(m):
        c = m["content"]
        return c[0]["text"] if isinstance(c, list) else (c or "")

    real = [i for i, m in enumerate(msgs) if m["role"] == "user" and not text_of(m).startswith("[autopilot]")
            and not text_of(m).startswith("Your reply was cut off") and not text_of(m).startswith("[system]") and not text_of(m).startswith("[review]")]
    last_user = real[-1]
    prompt = text_of(msgs[last_user])
    if "TIMECHECK" not in prompt:                    # the platform stamps user messages with the time; scripted scenarios match on the bare text
        import re as _r0
        prompt = _r0.sub(r"^\[\d{4}-\d{2}-\d{2} [^\]]*\]\s*", "", prompt)
    after = msgs[last_user + 1:]
    k = sum(1 for m in after if m["role"] == "tool")
    nudges = sum(1 for m in after if m["role"] == "user" and text_of(m).startswith("[autopilot]"))
    ws_nudges = sum(1 for m in after if m["role"] == "user" and text_of(m).startswith("[system]"))
    rev_nudges = sum(1 for m in after if m["role"] == "user" and text_of(m).startswith("[review]"))
    last_tool = next((m["content"] for m in reversed(after) if m["role"] == "tool"), "")
    tool_texts = [m["content"] for m in after if m["role"] == "tool"]

    # ---- sub-agent roles (prompts the main scenarios hand to sub-agents)
    if "WORLD ENGINE" in sysmsg and TOOLS_OFFERED[0] and ("LOOPFOREVER" in prompt or ("LOOKUP" in prompt and k == 0)):
        return "tools", [tool_call("web_search", {"query": "foggy harbor towns"})]
    if "WORLD ENGINE" in sysmsg:
        names = re.findall(r"^### (.+)$", prompt, re.M)
        n = len(re.findall(r"^\[.*?\] \S", prompt, re.M))
        say = "ZAP-INTERJECT" if "Nobody told you" in prompt and "STRANGER" in prompt else "tick"
        say += f", looked={k}"
        ev = [{"who": nm, "kind": "say" if i == 0 else "do", "text": f"{nm} reacts ({say}, log={n})"} for i, nm in enumerate(names[:2])]
        mem = {names[0]: f"remembered tick {n}"} if names else {}
        return "text", "Sure, here is the next beat:\n```json\n" + json.dumps({"events": ev, "memory": mem}) + "\n```"
    if "ECHOTASK" in prompt:
        return "text", f"echo report model={model}"
    if "TOODEEP" in prompt:
        return "text", "never reached"
    if "LEAFTASK" in prompt:
        if k == 0:
            return "tools", [task("too deep", "TOODEEP")]
        return "text", "leaf: " + last_tool[:70]
    if "NESTTASK" in prompt:
        if k == 0:
            return "tools", [task("leaf", "LEAFTASK")]
        return "text", "nest ok -> " + last_tool[:90]
    if "APPROVETASK" in prompt:
        if k == 0:
            return "tools", [tool_call("bash", {"command": "touch made-by-sub.txt"})]
        return "text", "sub finished: " + last_tool[:40]
    if "SUBTASK" in prompt:
        if k == 0:
            return "tools", [tool_call("bash", {"command": "ls"})]
        return "text", "subagent report: found files"

    if "TIMECHECK" in prompt:       # reports what time information the model actually received
        import re as _re
        if k == 0:
            return "tools", [tool_call("bash", {"command": "echo hi"})]
        st = _re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]", prompt)
        nw = _re.search(r"- Now: (\w+ \d{4}-\d{2}-\d{2} \d{2}:\d{2}) \(([\w/+-]+),", sysmsg)
        tl = _re.search(r"\n\[(\d{2}:\d{2}:\d{2})[^\]]*\]$", last_tool)
        return "text", f"[2020-01-01 00:00] stamp={st.group(1) if st else None} now={nw.group(1) if nw else None} tz={nw.group(2) if nw else None} tool={'yes' if tl else 'no'} note={'timestamps like' in sysmsg}"
    if "independent code reviewer" in prompt:      # the self-review sub-agent
        if k == 0:
            return "tools", [tool_call("bash", {"command": "ls"})]
        if "RVWBAD" in prompt:
            return "text", "1. calc.add ignores negative numbers.\n   Evidence: python3 -c 'import calc; print(calc.add(-1, 1))' printed 2.\n   Fix: return a + b."
        return "text", "LGTM"

    # ---- main scenarios
    if "RVW" in prompt:             # writes + runs code, then stops; self-review may send it back once
        code = "def add(a, b):\n    return a + b\n"
        if k == 0:
            return "tools", [tool_call("write", {"path": "calc.py", "content": code})]
        if k == 1:
            return "tools", [tool_call("bash", {"command": "python3 -c 'import calc; print(calc.add(2,3))'"})]
        return "text", "Fixed after review." if rev_nudges else "Done."
    if "fizzbuzz.py" in prompt:      # the built-in eval task #1
        code = ("def fizzbuzz(n):\n    out = []\n    for i in range(1, n + 1):\n"
                "        out.append('FizzBuzz' if i % 15 == 0 else 'Fizz' if i % 3 == 0 else 'Buzz' if i % 5 == 0 else str(i))\n"
                "    return out\nprint(fizzbuzz(15))\n")
        steps = [[tool_call("write", {"path": "fizzbuzz.py", "content": code})],
                 [tool_call("bash", {"command": "python3 fizzbuzz.py"})]]
        return ("tools", steps[k]) if k < len(steps) else ("text", "Done: fizzbuzz works.")
    if "fib.cpp" in prompt:          # the built-in eval task: C++
        code = '#include <cstdio>\nint main(){long a=0,b=1;for(int i=0;i<10;i++){std::printf(i?" %ld":"%ld",a);long t=a+b;a=b;b=t;}std::printf("\\n");}\n'
        steps = [[tool_call("write", {"path": "fib.cpp", "content": code})],
                 [tool_call("bash", {"command": "g++ -o fib fib.cpp && ./fib"})]]
        return ("tools", steps[k]) if k < len(steps) else ("text", "Done: compiled and ran fib.cpp.")
    if "Check your actual tools before answering" in prompt:      # eval task: knows its own tools
        if k == 0 and ws_nudges == 0:
            return "text", "I can't add MCP connectors myself, I have no way to do that. Skills and routines are not something I can create either."
        return "text", "Right, I do have them.\nTOOLS=connector_add,skill_create,routine_create"
    if "DENYCAM" in prompt:
        return "text", "I can't take photos with a camera, I'm just software."
    if "MULTI" in prompt:
        steps = [
            [tool_call("todo_write", {"todos": [{"content": "write hello", "status": "in_progress"}]})],
            [tool_call("write", {"path": "hello.py", "content": "print('hello')\n"})],
            [tool_call("read", {"path": "hello.py"})],
            [tool_call("edit", {"path": "hello.py", "old_string": "hello", "new_string": "hello world"})],
            [tool_call("bash", {"command": "python3 hello.py"})],
            [tool_call("grep", {"pattern": "world"})],
            [tool_call("write", {"path": "../escape.txt", "content": "x"})],       # must be rejected
            [tool_call("bash", {"command": "cat /proc/self/environ"})],           # must be denied
            [tool_call("git_sync", {"message": "mock work"})],
        ]
        return ("tools", steps[k]) if k < len(steps) else ("text", f"All steps done. Last tool said: {last_tool[:60]}")
    if "PARALLEL" in prompt:
        if k == 0:
            return "tools", [task("a", "ECHOTASK a", "lightning"), task("b", "ECHOTASK b"),
                             task("c", "ECHOTASK c", "mock:mock/ultra-550b"), task("d", "ECHOTASK d", "nonexistent-model")]
        return "text", "parallel done: " + " | ".join(tool_texts)
    if "NEST" in prompt:
        if k == 0:
            return "tools", [task("nest", "NESTTASK")]
        return "text", "nest result: " + last_tool
    if "SUBAPPROVE" in prompt:
        if k == 0:
            return "tools", [task("needs approval", "APPROVETASK")]
        return "text", "main saw: " + last_tool
    if "AUTOP" in prompt:
        if nudges >= 1:
            return "text", "DONE: finished after nudge"
        if k == 0:
            return "tools", [tool_call("write", {"path": "auto.txt", "content": "step1\n"})]
        return "text", "partial progress, more to do"
    if "EMBED" in prompt:
        if k == 0:
            return "tools", [tool_call("bash", {"command": "mkdir -p pics && python3 -c \"from PIL import Image; Image.new('RGB',(1200,1600),'red').save('pics/a.png'); Image.new('RGB',(200,200),'blue').save('pics/b.png')\" && echo hello > report.txt"})]
        if ws_nudges == 0:
            return "text", "Here you go.\n\n![Red box](ws:pics/a.png)\n![Blue box](ws:pics/b.png)\n\nThe report: \n\n[Report](ws:report.txt)\n\nand a ghost ![x](ws:ghost.png)"
        return "text", "Fixed.\n\n![Red box](ws:pics/a.png)\n![Blue box](ws:pics/b.png)\n\n[Report](ws:report.txt)\n\nSee also [the red one](ws:pics/a.png) inline.\n\nPlain lone image:\n\n![Plain red](ws:pics/a.png)\n\nLarge one:\n\n![Large red|l](ws:pics/a.png)\n\nand a mini ![mini|s](ws:pics/b.png) mid-sentence."
    if "IMGCARD" in prompt:
        if k == 0:
            return "tools", [tool_call("bash", {"command": "mkdir -p pics && python3 -c \"from PIL import Image; Image.new('RGB',(1200,1600),'red').save('pics/a.png')\""})]
        if k == 1:
            return "tools", [tool_call("annotate_image", {"path": "pics/a.png", "ops": [{"type": "rect", "x": 10, "y": 10, "w": 50, "h": 30}]})]
        return "text", "annotated"
    if "called Counter" in prompt:
        html = ("<!doctype html><html><head><meta name='viewport' content='width=device-width'><title>Counter</title></head><body><h1 id='n'>0</h1><button id='p'>+</button><button id='m'>-</button>"
                "<script>var n=Number(localStorage.getItem('n')||0);function r(){document.getElementById('n').textContent=n;localStorage.setItem('n',n)}"
                "document.getElementById('p').onclick=function(){n++;r()};document.getElementById('m').onclick=function(){n--;r()};r()</script></body></html>")
        if k == 0:
            return "tools", [tool_call("create_app", {"name": "Counter", "icon": "🔢", "html": html})]
        return "text", "Counter is ready."
    if "VGATE" in prompt:           # writes code and tries to stop; only runs it after being sent back ("VGATEBAD" never does)
        code = "def add(a, b):\n    return a + b\n"
        if k == 0:
            return "tools", [tool_call("write", {"path": "calc.py", "content": code})]
        if k == 1 and ws_nudges >= 1 and "VGATEBAD" not in prompt:
            return "tools", [tool_call("bash", {"command": "python3 -c 'import calc; print(calc.add(2,3))'"})]
        return "text", "All done, it works." if ws_nudges < 5 else "still not running it"
    if "ASKUSER" in prompt:
        if k == 0:
            return "tools", [tool_call("ask_user", {"questions": [
                {"question": "Which database?", "header": "DB", "options": [{"label": "SQLite (Recommended)", "description": "no server"}, {"label": "Postgres"}]},
                {"question": "Which extras?", "multiSelect": True, "options": [{"label": "Auth"}, {"label": "Dark mode"}, {"label": "Export"}]}]})]
        return "text", "got: " + last_tool
    if "ASKBAD" in prompt:
        if k == 0:
            return "tools", [tool_call("ask_user", {"questions": [{"question": "only one option?", "options": [{"label": "x"}]}]})]
        return "text", "bad: " + last_tool[:80]
    if "MAKEACT" in prompt:
        html = ("<!doctype html><html><head><title>Quick</title></head><body><h1>Quick</h1><button id='b' onclick=\"agent.act('save this note: hi').then(function(r){document.title=r})\">save</button></body></html>")
        if k == 0:
            return "tools", [tool_call("create_app", {"name": "Quick Note", "icon": "📝", "html": html + ("<!--v2-->" if "v2" in prompt else ""), **({"id": "quick-note"} if "v2" in prompt else {})})]
        return "text", "made it"
    if "ACTBASH" in prompt:
        if k == 0:
            return "tools", [tool_call("bash", {"command": "echo hi > x.txt"})]
        return "text", "bash was refused: " + (last_tool or "")[:120]
    if "save this note" in prompt and "called 'Quick Note'" in prompt:
        return "text", "Saved the note."
    if "MAKEAPP" in prompt:
        html = ("<!doctype html><html><head><meta name='viewport' content='width=device-width'><title>Tip</title></head><body><h1 id='t'>Tip calc</h1>"
                "<button id='b' onclick=\"agent.ask('hi').then(function(r){document.getElementById('t').textContent=r})\">ask</button>"
                "<div id='ls'></div><script>var prev=localStorage.getItem('k');localStorage.setItem('k','seen');sessionStorage.setItem('s','x');"
                "document.getElementById('ls').textContent=String(prev)+'|'+sessionStorage.getItem('s')+'|'+localStorage.length;"
                "agent.get('n').then(function(v){agent.set('n',(v||0)+1)})</script></body></html>")
        if k == 0:
            return "tools", [tool_call("create_app", {"name": "Tip Calc", "icon": "🧮", "description": "splits a bill", "html": html})]
        if k == 1:
            return "tools", [tool_call("create_app", {"id": "tip-calc", "name": "Tip Calc", "html": html.replace("Tip calc", "Tip calc v2")})]
        return "text", "App ready:\n\n[Tip Calc](app:tip-calc)\n\nor tap [it](app:tip-calc) inline, or a ghost: [Nope](app:ghost-app)"
    if "SHOWMODEL" in prompt:
        if k == 0:
            return "tools", [tool_call("bash", {"command": "python3 -c \"import trimesh; trimesh.creation.box((10,10,10)).export('cube.stl')\""})]
        if k == 1:
            return "tools", [tool_call("view_3d", {"path": "cube.stl"})]
        return "text", "model ready"
    if "SHOWPAGE" in prompt:
        if k == 0:
            return "tools", [tool_call("write", {"path": "index.html", "content": "<h1>mock page</h1><script>document.title='ran'</script>"})]
        if k == 1:
            return "tools", [tool_call("preview", {"path": "index.html"})]
        return "text", "page ready"
    if "SHARE" in prompt:
        if k == 0:
            return "tools", [tool_call("write", {"path": "report.txt", "content": "the report\n"})]
        if k == 1:
            return "tools", [tool_call("share_file", {"path": "report.txt"})]
        return "text", "shared"
    if "INLINE" in prompt:
        if k == 0:
            return "inline", '<tool_call>{"name": "bash", "arguments": {"command": "echo inline-ok"}}</tool_call>'
        return "text", "inline worked"
    if "BADJSON" in prompt:
        if k == 0:
            return "tools", [tool_call("read", None, raw='{"path": "x.txt"')]
        return "text", "recovered: " + last_tool[:80]
    if "SUB" in prompt:
        if k == 0:
            return "tools", [task("look around", "SUBTASK list files")]
        return "text", "sub-agent said: " + last_tool[:80]
    if "PLAN" in prompt:
        if k == 0:
            return "tools", [tool_call("write", {"path": "a.txt", "content": "nope"})]   # denied in plan mode
        if k == 1:
            return "tools", [tool_call("exit_plan_mode", {"plan": "1. write a.txt"})]
        if k == 2:
            return "tools", [tool_call("write", {"path": "a.txt", "content": "planned"})]
        return "text", "plan executed"
    if "BIG" in prompt:
        return "text", "x" * 900 + " big reply"
    return "text", "Hello from the mock model. You said: " + str(prompt)[:60]


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    if body["model"] == "boom":
        SEEN["models"].append("boom")
        return JSONResponse({"error": {"message": "nope"}}, status_code=401)
    SEEN["thinking_param"].append(body.get("chat_template_kwargs"))
    SEEN["models"].append(body["model"])
    TOOLS_OFFERED[0] = bool(body.get("tools"))
    kind, payload = scenario(body["messages"], body["model"])

    async def gen():
        if kind == "text":
            yield sse({"reasoning_content": "thinking about it. "})
            for i in range(0, len(payload), 12):
                yield sse({"content": payload[i:i + 12]})
            yield sse({}, "stop")
        elif kind == "inline":
            yield sse({"content": payload})
            yield sse({}, "stop")
        else:
            for n, c in enumerate(payload):
                cid = "call_" + uuid.uuid4().hex[:8]
                a = c["args"]
                yield sse({"tool_calls": [{"index": n, "id": cid, "type": "function",
                                           "function": {"name": c["name"], "arguments": a[:len(a) // 2]}}]})
                yield sse({"tool_calls": [{"index": n, "function": {"arguments": a[len(a) // 2:]}}]})
            yield sse({}, "tool_calls")
        yield "data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/seen")
async def seen():
    return SEEN
