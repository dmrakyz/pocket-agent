"""Tool forging: the agent writes a real tool (Python), proves it with tests, you approve it, and it joins the toolbelt as forged_<name>.

Stored in the state repo (kv 'forged_tools'). Each call runs in a throwaway subprocess (see forge_runner.py): stripped env, limits, scratch dir, no sockets
unless the tool declared network use. That is best-effort containment for code an LLM wrote and you approved, not a hardened sandbox."""
import asyncio
import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

from . import kv

PREFIX = "forged_"
NAME_RX = re.compile(r"^[a-z][a-z0-9_]{2,30}$")
MAX_CODE = 20000
MAX_OUT = 20000
MAX_TOOLS = 25
RUNNER = Path(__file__).with_name("forge_runner.py")
RESERVED = {"read", "write", "edit", "bash", "list", "tool", "run", "test"}


class ForgeError(Exception):
    pass


def all_tools() -> dict:
    return kv.get("forged_tools", {}) or {}


def get(name: str) -> dict | None:
    return all_tools().get(name)


def enabled_tools() -> list[dict]:
    return [t for t in all_tools().values() if t.get("enabled")]


def schemas(fn) -> list[dict]:
    """OpenAI-style tool schemas for every enabled forged tool (fn is tools._fn)."""
    out = []
    for t in enabled_tools():
        props = t.get("params") or {}
        out.append(fn(PREFIX + t["name"], t["description"] + (" [custom tool; uses the network]" if t.get("network") else " [custom tool]"),
                      props, t.get("required") or []))
    return out


def validate(spec: dict) -> dict:
    name = str(spec.get("name") or "").strip().lower()
    if not NAME_RX.match(name) or name in RESERVED:
        raise ForgeError("name must be 3-31 chars of a-z, 0-9, _ and start with a letter (and not be a generic word like 'run')")
    desc = str(spec.get("description") or "").strip()
    if len(desc) < 10:
        raise ForgeError("give a description of at least 10 characters saying when to use the tool")
    code = str(spec.get("code") or "")
    if not code.strip() or len(code) > MAX_CODE:
        raise ForgeError(f"code is required and must be under {MAX_CODE} characters")
    if not re.search(r"^def run\s*\(", code, re.M):
        raise ForgeError("code must define  def run(args):  that returns a string or JSON-able value")
    try:
        compile(code, "forged_tool.py", "exec")
    except SyntaxError as e:
        raise ForgeError(f"syntax error on line {e.lineno}: {e.msg}")
    params = spec.get("parameters") or {}
    if not isinstance(params, dict) or any(not isinstance(v, dict) or v.get("type") not in ("string", "number", "integer", "boolean", "array", "object") for v in params.values()):
        raise ForgeError("parameters must be an object of {argument: {type: string|number|integer|boolean|array|object, description}}")
    required = [r for r in (spec.get("required") or []) if r in params]
    tests = spec.get("tests") or []
    if not isinstance(tests, list) or not tests or not all(isinstance(t, dict) and isinstance(t.get("args", {}), dict) for t in tests):
        raise ForgeError("tests is required: a list of {args: {...}, expect: 'text the result must contain'}. Write at least one that would fail if the tool were broken")
    return {"name": name, "description": desc, "code": code, "params": params, "required": required,
            "network": bool(spec.get("network")), "tests": [{"args": t.get("args", {}), "expect": str(t.get("expect", ""))} for t in tests[:8]]}


async def sandbox(code: str, args: dict, network: bool = False, timeout: float = 20) -> dict:
    """Run run(args) in a fresh process. Returns {'ok': bool, 'result'|'error': ..., 'stderr': str}."""
    tmp = tempfile.mkdtemp(prefix="forge-")
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": tmp, "FORGE_CWD": tmp, "MPLBACKEND": "Agg"}
    try:
        p = await asyncio.create_subprocess_exec(sys.executable, "-I", str(RUNNER), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.PIPE, env=env, cwd=tmp)
        try:
            out, err = await asyncio.wait_for(p.communicate(json.dumps({"code": code, "args": args, "network": network, "cpu": int(timeout)}).encode()), timeout)
        except asyncio.TimeoutError:
            try:
                p.kill()
            except ProcessLookupError:
                pass
            await p.wait()
            return {"ok": False, "error": f"timed out after {int(timeout)}s", "stderr": ""}
        text = out.decode("utf8", "replace")
        mark = "\x00RESULT\x00"
        if mark not in text:
            return {"ok": False, "error": "the tool crashed or hit a resource limit before returning", "stderr": err.decode("utf8", "replace")[-800:]}
        res = json.loads(text.rsplit(mark, 1)[1].strip().splitlines()[0])
        res["stderr"] = err.decode("utf8", "replace")[-800:]
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _text(result) -> str:
    s = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    return s if len(s) <= MAX_OUT else s[:MAX_OUT] + f"\n… (cut, {len(s)} chars)"


async def run_tool(name: str, args: dict) -> tuple[str, bool]:
    """Call an enabled forged tool. Returns (text, is_error)."""
    t = get(name)
    if not t or not t.get("enabled"):
        return f"forged tool {name!r} is not available or is switched off", True
    res = await sandbox(t["code"], args or {}, bool(t.get("network")), 60 if t.get("network") else 20)
    d = all_tools()
    if name in d:
        d[name]["uses"] = d[name].get("uses", 0) + 1
        d[name]["last_error"] = "" if res["ok"] else res.get("error", "")[:200]
        await kv.put("forged_tools", d)
    if not res["ok"]:
        return f"Error in forged tool: {res.get('error')}" + (f"\n{res['stderr']}" if res.get("stderr") else ""), True
    return _text(res.get("result")), False


async def add(spec: dict) -> dict:
    """Validate, run the tests in the sandbox, and only then store (enabled). Raises ForgeError with a precise reason."""
    v = validate(spec)
    d = all_tools()
    if v["name"] not in d and len(d) >= MAX_TOOLS:
        raise ForgeError(f"you already have {MAX_TOOLS} forged tools; remove one in Settings first")
    for i, t in enumerate(v["tests"], 1):
        r = await sandbox(v["code"], t["args"], v["network"], 30 if v["network"] else 15)
        if not r["ok"]:
            raise ForgeError(f"test {i} crashed: {r.get('error')}" + (f"\n{r['stderr']}" if r.get("stderr") else ""))
        if t["expect"] and t["expect"] not in _text(r.get("result")):
            raise ForgeError(f"test {i} failed: expected the result to contain {t['expect']!r} but got {_text(r.get('result'))[:300]!r}")
    old = d.get(v["name"]) or {}
    d[v["name"]] = {**v, "enabled": True, "created": old.get("created") or int(time.time()), "updated": int(time.time()), "uses": old.get("uses", 0), "last_error": ""}
    await kv.put("forged_tools", d)
    return d[v["name"]]


async def set_enabled(name: str, on: bool) -> bool:
    d = all_tools()
    if name not in d:
        return False
    d[name]["enabled"] = on
    await kv.put("forged_tools", d)
    return True


async def remove(name: str) -> bool:
    d = all_tools()
    if name not in d:
        return False
    d.pop(name)
    await kv.put("forged_tools", d)
    return True


def public(t: dict, code: bool = False) -> dict:
    out = {k: t.get(k) for k in ("name", "description", "network", "enabled", "uses", "created", "updated", "last_error", "params", "required")}
    if code:
        out["code"] = t.get("code", "")
        out["tests"] = t.get("tests", [])
    return out
