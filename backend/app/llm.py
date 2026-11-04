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


class KeyPool:
    """Several API keys for one provider. Every key has its own requests-per-minute window; a request goes to the
    key with the most headroom, and a key that got a 429 sits out for a while (Retry-After, default 30 s)."""

    def __init__(self, keys: list, rpm: int):
        self.keys = list(keys)
        self.lims = [Limiter(rpm) for _ in self.keys]
        self.until = [0.0] * len(self.keys)

    def usable(self) -> int:
        now = time.monotonic()
        return sum(1 for u in self.until if u <= now)

    def cool(self, i: int, secs: float):
        self.until[i] = time.monotonic() + max(1.0, min(float(secs), 300.0))

    def _load(self, i: int) -> int:
        now = time.monotonic()
        return sum(1 for t in self.lims[i].times if now - t < self.lims[i].window)

    async def acquire(self):
        """-> (index, key) of a key that is allowed to take one more request right now."""
        while True:
            now = time.monotonic()
            ok = [i for i in range(len(self.keys)) if self.until[i] <= now]
            if ok:
                if not self.lims[0].rpm:
                    i = min(ok, key=lambda j: self._load(j))
                    return i, self.keys[i]
                free = [i for i in ok if self._load(i) < self.lims[i].rpm]
                if free:
                    i = min(free, key=lambda j: self._load(j))
                    await self.lims[i].wait()
                    return i, self.keys[i]
                await asyncio.sleep(0.2)
            else:
                await asyncio.sleep(max(0.1, min(self.until) - now))

    def status(self) -> list:
        now = time.monotonic()
        return [{"key": i + 1, "cooling": round(max(0, self.until[i] - now)), "recent": self._load(i)} for i in range(len(self.keys))]


_pools: dict[tuple, KeyPool] = {}


def pool(name: str) -> KeyPool:
    prov = config.providers()[name]
    keys = tuple(prov.get("api_keys") or [prov["api_key"]])
    if (name, keys) not in _pools:
        _pools[(name, keys)] = KeyPool(keys, int(config.rpm_limits().get(name, 0)))
    return _pools[(name, keys)]


def key_status() -> dict:
    return {n: pool(n).status() for n in config.providers() if len(config.providers()[n].get("api_keys") or [1]) > 1}


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
        raise LLMError("No provider configured. Set KIMI_API_KEYS, NVIDIA_API_KEY, or another provider key in Render.")
    return provs[0], ref


def _headers(prov: dict, key: str = "") -> dict:
    return {"Authorization": f"Bearer {key or prov['api_key']}", "Content-Type": "application/json"}


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


async def select_default_model(models: list[dict]) -> str:
    """Prefer standard Kimi K3.1 Pro, then Nemotron Ultra, then other available models."""
    if config.DEFAULT_MODEL:
        return config.DEFAULT_MODEL
    kimi = next((m for m in models if not m.get("error") and m.get("provider") == "kimi"
                 and m.get("id") == "kimi-k3.1-pro"), None)
    if kimi:
        return kimi["ref"]
    return pick(models, "nemotron-3-ultra", "ultra", "nemotron-3.5-super", "super") or (models[0]["ref"] if models else "")


def select_fallback_model(models: list[dict]) -> str:
    """Prefer Nemotron Ultra as Kimi's fallback; use a smaller model only if Ultra is absent."""
    if config.FALLBACK_MODEL:
        return config.FALLBACK_MODEL
    return pick(models, "nemotron-3-ultra", "ultra", "lightning", "nano") or ""


async def default_model() -> str:
    return select_default_model(await list_models())


async def fallback_model() -> str:
    return select_fallback_model(await list_models())


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


def strip_private(messages: list[dict], preserve_reasoning: bool = False) -> list[dict]:
    """Drop local bookkeeping; Kimi additionally needs its original reasoning_content on later turns."""
    return [{k: v for k, v in m.items() if not k.startswith("_") and (preserve_reasoning or k != "reasoning_content")}
            for m in messages]


def kimi_reasoning_effort(thinking, effort=None) -> str:
    """Map Pocket Agent's Off/Medium/High control to Kimi's always-on low/high/max reasoning."""
    if thinking is False:
        return "low"                 # Kimi cannot disable thinking; use its lightest setting
    if str(effort or "").lower() in ("medium", "med"):
        return "high"
    if str(effort or "").lower() == "low":
        return "low"
    return "max"                     # Pocket Agent's default High maps to Kimi's maximum effort


def request_body(prov_name: str, model: str, messages, tools, thinking, max_tokens, effort=None) -> dict:
    """Build provider-specific chat payloads without leaking one provider's extensions to another."""
    body = {"model": model, "messages": strip_private(messages, preserve_reasoning=(prov_name == "kimi")), "stream": True}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if prov_name == "kimi":
        # Moonshot uses max_completion_tokens for output caps and rate-limit reservation.
        body["max_completion_tokens"] = int(max_tokens or 16384)
        body["reasoning_effort"] = kimi_reasoning_effort(thinking, effort)
    elif max_tokens:
        body["max_tokens"] = max_tokens
    if thinking is not None and prov_name == "nvidia":
        kw = {"enable_thinking": bool(thinking)}
        if thinking and effort == "medium":
            kw["medium_effort"] = True     # Nemotron 3 Ultra: fewer reasoning tokens than full thinking
        body["chat_template_kwargs"] = kw
    return body


async def _stream_once(prov_name: str, model: str, messages, tools, on_text, on_reasoning, thinking, max_tokens, effort=None):
    prov = config.providers()[prov_name]
    body = request_body(prov_name, model, messages, tools, thinking, max_tokens, effort)

    content, reasoning, tcalls, finish, usage = [], [], {}, None, None
    kp = pool(prov_name)
    ki, key = await kp.acquire()
    url = prov["base_url"].rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        async with c.stream("POST", url, headers=_headers(prov, key), json=body) as r:
            if r.status_code >= 400:
                text = (await r.aread()).decode("utf-8", "replace")[:600]
                if r.status_code == 429:
                    try:
                        ra = float(r.headers.get("retry-after") or 30)
                    except ValueError:
                        ra = 30.0
                    kp.cool(ki, ra)
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
    if prov_name == "kimi":
        # Kimi requires the complete assistant message, including reasoning_content, on subsequent turns.
        msg["reasoning_content"] = "".join(reasoning)
    elif reasoning:
        msg["_reasoning"] = "".join(reasoning)
    return {"message": msg, "finish_reason": finish, "usage": usage}


Notify = Callable[[str], Awaitable[None]]
_no_think: set = set()
_no_effort: set = set()


async def chat(model_ref: str, messages: list[dict], tools: list[dict], on_text, on_reasoning,
               notify: Notify, thinking=None, max_tokens=None, effort=None, fallback: bool = True):
    """Streaming chat with retries and one fallback-model attempt (unless fallback=False: pinned models never get swapped). Returns {message, finish_reason, usage}."""
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
            if e.status == 429 and pool(prov).usable() > 0:        # another key is free: go again right now
                await notify("one API key is rate-limited; switching to another")
                continue
            if e.status in (429, 503) and attempt >= 1:     # overloaded/rate-limited: a second model beats a longer wait
                break
            await notify(f"{e} — retrying in {delays[attempt]}s")
            await asyncio.sleep(delays[attempt])
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last = LLMError(f"network error: {type(e).__name__}")
            if attempt == len(delays):
                break
            await notify(f"network hiccup ({type(e).__name__}) — retrying in {delays[attempt]}s")
            await asyncio.sleep(delays[attempt])

    fb = await fallback_model() if fallback else ""
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
