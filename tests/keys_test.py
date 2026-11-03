"""API key rotation: per-key limits, least-busy pick, 429 cooldown + instant switch, keys never leak."""
import asyncio, json, os, sys, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.update({"APP_PASSWORD": "pw", "DATA_DIR": "/tmp/agent-keys", "NVIDIA_API_KEY": "kA", "NVIDIA_API_KEYS": "kB, kC,kA",
                   "NVIDIA_BASE_URL": "http://127.0.0.1:9190/v1", "LLM_RPM": json.dumps({"nvidia": 4})})
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
fake = FastAPI(); SEEN = []; BAD = set()


@fake.post("/v1/chat/completions")
async def chat(request: Request):
    key = request.headers["authorization"].split()[1]
    SEEN.append(key)
    if key in BAD:
        return JSONResponse({"error": "slow down"}, status_code=429, headers={"Retry-After": "30"})
    def gen():
        yield 'data: ' + json.dumps({"choices": [{"delta": {"content": "hi from " + key}, "finish_reason": "stop"}]}) + "\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
threading.Thread(target=lambda: uvicorn.run(fake, port=9190, log_level="warning"), daemon=True).start(); time.sleep(1.5)
from app import config, llm

fails = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else "")); 
    if not c: fails.append(n)

async def ask():
    notes = []
    async def n(t): notes.append(t)
    async def nop(_): pass
    r = await llm.chat("nvidia:m", [{"role": "user", "content": "x"}], [], nop, nop, n)
    return r["message"]["content"], notes

async def main():
    check("keys are de-duplicated and ordered, first = NVIDIA_API_KEY", config.providers()["nvidia"]["api_keys"] == ["kA", "kB", "kC"], str(config.providers()["nvidia"]["api_keys"]))
    for _ in range(6): await ask()
    check("load spreads over all keys", {k: SEEN.count(k) for k in "kA kB kC".split()} == {"kA": 2, "kB": 2, "kC": 2}, str(SEEN))
    SEEN.clear(); llm._pools.clear(); BAD.add("kA"); BAD.add("kB")
    t0 = time.monotonic()
    outs = [await ask() for _ in range(3)]
    check("429 on a key switches to another one right away (no 2s+ waiting)", time.monotonic() - t0 < 2 and all(o[0] == "hi from kC" for o in outs), str(outs))
    check("rate-limited keys sit out (each hit at most once while cooling)", SEEN.count("kA") <= 1 and SEEN.count("kB") <= 1, str(SEEN))
    st = llm.key_status()["nvidia"]
    check("status shows cooling keys by number, never the key", st[0]["cooling"] > 0 and st[1]["cooling"] > 0 and st[2]["cooling"] == 0 and "kA" not in json.dumps(st))
    check("a switch is announced to the user", any("switching" in x for x in outs[0][1]) or any("switching" in x for o in outs for x in o[1]))
    # per-key limit: 4 rpm each => 12 requests all fine, the 13th has to wait
    SEEN.clear(); BAD.clear(); llm._pools.clear()
    t0 = time.monotonic()
    for _ in range(12): await ask()
    check("3 keys x 4 rpm = 12 requests with no waiting", time.monotonic() - t0 < 3, str(time.monotonic() - t0))
    llm._pools.clear(); SEEN.clear(); BAD.update({"kA", "kB", "kC"})
    try:
        await asyncio.wait_for(ask(), 20)
        check("all keys limited -> error surfaces after retries", False)
    except Exception as e:
        check("all keys limited -> error surfaces after retries", "429" in str(e), str(e))
    check("secret env var is hidden from subprocesses", "NVIDIA_API_KEYS" in config.SECRET_ENV_KEYS)
asyncio.run(main())
print("\nALL PASSED" if not fails else "\nFAILED: " + str(fails)); sys.exit(1 if fails else 0)
