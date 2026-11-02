"""OpenAI-compatible streaming chat client with retry, fallback and lenient tool-call parsing."""
import asyncio
import json
import re
import time
import uuid
from collections import deque
from typing import Awaitable, Callable, Optional

import httpx

from . import config

TIMEOUT = httpx.Timeout(600.0, connect=30.0)


class Limiter:
    """Sliding-window requests-per-window limiter, shared by every agent/subagent using a provider."""

    def __init__(self, rpm: int, window: float = 60.0):
        self.rpm, self.window, self.times, self.lock = rpm, window, deque(), asyncio.Lock()

    async def wait(self):
        if not self.rpm:
            return
        async with self.lock:
            while True:
                now = time.monotonic()
                while self.times and now - self.times[0] >= self.window:
                    self.times.popleft()
                if len(self.times) < self.rpm:
                    self.times.append(now)
                    return
                await asyncio.sleep(max(0.05, self.window - (now - self.times[0]) + 0.05))


_limiters: dict[str, Limiter] = {}


def limiter(name: str) -> Limiter:
    if name not in _limiters:
        _limiters[name] = Limiter(int(config.rpm_limits().get(name, 0)))
    return _limiters[name]


class LLMError(Exception):
    def __init__(self, msg: str, status: int = 0):
        super().__init__(msg)
        self.status = status


def split_ref(ref: str):
    """'nvidia:nvidia/nemotron-x' -> ('nvidia', 'nvidia/nemotron-x')"""
    if ":" in ref:
        prov, model = ref.split(":", 1)
        if prov in config.providers():
            return prov, model
    provs = list(config.providers())
    if not provs:
        raise LLMError("No provider configured. Set NVIDIA_API_KEY (or another *_API_KEY) in Render.")
    return provs[0], ref


def _headers(prov: dict) -> dict:
    return {"Authorization": f"Bearer {prov['api_key']}", "Content-Type": "application/json"}


_models_cache: dict = {}


async def list_models(force: bool = False) -> list[dict]:
    """Ask every configured provider which models its key can see."""
    if _models_cache and not force:
        return _models_cache["v"]
    out = []

    async def one(name, prov):
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(prov["base_url"].rstrip("/") + "/models", headers=_headers(prov))
                r.raise_for_status()
                data = r.json().get("data", [])
                return [{"ref": f"{name}:{m['id']}", "provider": name, "id": m["id"]} for m in data if "id" in m]
        except Exception as e:  # noqa: BLE001
            return [{"ref": f"{name}:(error)", "provider": name, "id": f"(could not list: {type(e).__name__})",
                     "error": True}]

    results = await asyncio.gather(*[one(n, p) for n, p in config.providers().items()])
    for r in results:
        out.extend(r)
    out.sort(key=lambda m: (m["provider"], m["id"]))
    _models_cache["v"] = out
    return out


def pick(models: list[dict], *needles: str) -> Optional[str]:
    ok = [m for m in models if not m.get("error")]
    for n in needles:
        for m in ok:
            if n in m["id"].lower():
                return m["ref"]
    return None


async def default_model() -> str:
    if config.DEFAULT_MODEL:
        return config.DEFAULT_MODEL
    ms = await list_models()
    return pick(ms, "nemotron-3-ultra", "ultra", "nemotron-3.5-super", "super") or (ms[0]["ref"] if ms else "")


async def fallback_model() -> str:
    if config.FALLBACK_MODEL:
        return config.FALLBACK_MODEL
    ms = await list_models()
    return pick(ms, "lightning", "nano") or ""


# ---------- tool-call fallback parsing (some open models emit them as text) ----------
_INLINE = [
    re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S),
    re.compile(r"<TOOLCALL>\s*(.*?)\s*</TOOLCALL>", re.S),
]


def parse_inline_tool_calls(content: str):
    calls = []
    clean = content
    for rx in _INLINE:
        for m in rx.finditer(content):
            try:
                obj = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            objs = obj if isinstance(obj, list) else [obj]
            for o in objs:
                if isinstance(o, dict) and "name" in o:
                    args = o.get("arguments", o.get("parameters", {}))
                    calls.append({
                        "id": "call_" + uuid.uuid4().hex[:12], "type": "function",
                        "function": {"name": o["name"],
                                     "arguments": args if isinstance(args, str) else json.dumps(args)},
                    })
            clean = clean.replace(m.group(0), "")
    return clean.strip(), calls


def strip_private(messages: list[dict]) -> list[dict]:
    """Drop our own bookkeeping keys (anything starting with '_') before sending to a provider."""
    return [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]


async def _stream_once(prov_name: str, model: str, messages, tools, on_text, on_reasoning, thinking, max_tokens, effort=None):
    prov = config.providers()[prov_name]
    body = {"model": model, "messages": strip_private(messages), "stream": True}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if max_tokens:
        body["max_tokens"] = max_tokens
    if thinking is not None and prov_name == "nvidia":
        kw = {"enable_thinking": bool(thinking)}
        if thinking and effort == "medium":
            kw["medium_effort"] = True     # Nemotron 3 Ultra: fewer reasoning tokens than full thinking
        body["chat_template_kwargs"] = kw

    content, reasoning, tcalls, finish, usage = [], [], {}, None, None
    await limiter(prov_name).wait()
    url = prov["base_url"].rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        async with c.stream("POST", url, headers=_headers(prov), json=body) as r:
            if r.status_code >= 400:
                text = (await r.aread()).decode("utf-8", "replace")[:600]
                raise LLMError(f"{prov_name} HTTP {r.status_code}: {text}", r.status_code)
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    ev = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if ev.get("usage"):
                    usage = ev["usage"]
                for ch in ev.get("choices", []):
                    d = ch.get("delta") or {}
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
                    rc = d.get("reasoning_content") or d.get("reasoning")
                    if rc:
                        reasoning.append(rc)
                        await on_reasoning(rc)
                    if d.get("content"):
                        content.append(d["content"])
                        await on_text(d["content"])
                    for tc in d.get("tool_calls") or []:
                        i = tc.get("index", 0)
                        slot = tcalls.setdefault(i, {"id": "", "type": "function",
                                                     "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]

    text = "".join(content)
    calls = [tcalls[i] for i in sorted(tcalls)]
    for c in calls:
        if not c["id"]:
            c["id"] = "call_" + uuid.uuid4().hex[:12]
        if not c["function"]["arguments"]:
            c["function"]["arguments"] = "{}"
    if not calls and text and ("<tool_call>" in text or "<TOOLCALL>" in text):
        text, calls = parse_inline_tool_calls(text)
    msg = {"role": "assistant", "content": text or None}
    if calls:
        msg["tool_calls"] = calls
    if reasoning:
        msg["_reasoning"] = "".join(reasoning)
    return {"message": msg, "finish_reason": finish, "usage": usage}


Notify = Callable[[str], Awaitable[None]]
_no_think: set = set()
_no_effort: set = set()


async def chat(model_ref: str, messages: list[dict], tools: list[dict], on_text, on_reasoning,
               notify: Notify, thinking=None, max_tokens=None, effort=None):
    """Streaming chat with retries and one fallback-model attempt. Returns {message, finish_reason, usage}."""
    prov, model = split_ref(model_ref)
    if (prov, model) in _no_think:
        thinking = None
    if (prov, model) in _no_effort:
        effort = None
    delays = [2, 5, 12, 25]
    last: Exception = LLMError("unknown")
    tried_no_thinking = False
    for attempt in range(len(delays) + 1):
        try:
            return await _stream_once(prov, model, messages, tools, on_text, on_reasoning, thinking, max_tokens, effort)
        except LLMError as e:
            last = e
            if e.status == 400 and thinking and effort:
                _no_effort.add((prov, model))
                effort = None              # this model doesn't know the effort flag; keep thinking on, retry
                continue
            if e.status == 400 and thinking is not None and not tried_no_thinking:
                tried_no_thinking = True
                _no_think.add((prov, model))
                thinking = None            # provider rejected chat_template_kwargs; remember and retry plain
                continue
            if e.status not in (408, 409, 425, 429, 500, 502, 503, 504) or attempt == len(delays):
                break
            await notify(f"{e} — retrying in {delays[attempt]}s")
            await asyncio.sleep(delays[attempt])
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last = LLMError(f"network error: {type(e).__name__}")
            if attempt == len(delays):
                break
            await notify(f"network hiccup ({type(e).__name__}) — retrying in {delays[attempt]}s")
            await asyncio.sleep(delays[attempt])

    fb = await fallback_model()
    if fb and fb != model_ref and fb != f"{prov}:{model}":
        await notify(f"{model} keeps failing ({last}). Falling back to {fb} for this step.")
        fprov, fmodel = split_ref(fb)
        return await _stream_once(fprov, fmodel, messages, tools, on_text, on_reasoning, None, max_tokens)
    raise last


async def summarize(model_ref: str, text: str, instruction: str) -> str:
    """One-shot non-streaming helper used for compaction."""
    async def noop(_):
        return None
    out = await chat(model_ref, [{"role": "system", "content": instruction},
                                 {"role": "user", "content": text}], [], noop, noop, noop, thinking=False,
                     max_tokens=4000)
    return out["message"].get("content") or ""
