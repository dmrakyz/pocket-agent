"""Watchers: the agent keeps an eye on something and taps you only when it matters.
 - url watchers are cheap (no model call): fetch a public page on a schedule, compare it, push when it changes or a phrase appears/disappears.
 - 'prompt' watchers are quiet routines (see routines.py): the agent runs a prompt on a schedule and says NOTHING when there is no news."""
import asyncio
import difflib
import hashlib
import re
import time
import uuid

from . import kv, push

KEY = "watchers"
MAX = 12
_task = None


def all_watchers() -> list:
    return kv.get(KEY, []) or []


def clean(body: dict) -> dict:
    url = str(body.get("url") or "").strip()
    if not re.match(r"^https?://", url):
        raise ValueError("url must start with http:// or https://")
    cond = str(body.get("condition") or "change").strip()
    if cond != "change" and not re.match(r"^(contains|absent):.+", cond):
        raise ValueError("condition must be 'change', 'contains:<text>' or 'absent:<text>'")
    return {"id": uuid.uuid4().hex[:8], "kind": "url", "name": (str(body.get("name") or url)[:60]), "url": url[:500], "condition": cond[:200],
            "every": max(5, min(int(body.get("every_minutes") or 30), 60 * 24 * 14)), "enabled": True, "created": time.time(),
            "last_check": 0, "last_hash": "", "last_text": "", "fails": 0, "hits": 0}


def _norm(text: str) -> str:
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def _diff(old: str, new: str) -> str:
    d = [l for l in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0) if l[:1] in "+-" and l[:3] not in ("+++", "---")]
    return " | ".join(x.strip()[:90] for x in d[:4])


async def fetch(url: str) -> str:
    from . import tools
    return _norm(await tools.t_web_fetch(None, {"url": url}))


async def check(w: dict, now: float | None = None) -> str | None:
    """Run one check. Returns the notification text if the user should be told, else None. Mutates w."""
    w["last_check"] = now or time.time()
    try:
        text = await fetch(w["url"])
    except Exception as e:  # noqa: BLE001
        w["fails"] = w.get("fails", 0) + 1
        w["last_status"] = f"error: {str(e)[:120]}"
        return f"I can't reach {w['url']} any more ({str(e)[:80]})" if w["fails"] == 3 else None
    w["fails"], w["last_status"] = 0, "ok"
    h = hashlib.sha256(text.encode()).hexdigest()
    first = not w.get("last_hash")
    cond, msg = w.get("condition", "change"), None
    if cond == "change":
        if not first and h != w["last_hash"]:
            msg = "changed: " + (_diff(w.get("last_text", ""), text) or "the page content is different")
    else:
        kind, needle = cond.split(":", 1)
        present = needle.lower() in text.lower()
        was = needle.lower() in (w.get("last_text") or "").lower() if not first else None
        if kind == "contains" and present and was is not True:
            msg = f"now mentions “{needle}”"
        elif kind == "absent" and not present and was is not False:
            msg = f"no longer mentions “{needle}”"
    w["last_hash"], w["last_text"] = h, text[:20000]
    if msg:
        w["hits"] = w.get("hits", 0) + 1
        return f"{w['name']} {msg}"
    return None


async def run_due(now: float | None = None):
    now = now or time.time()
    ws = all_watchers()
    changed = False
    for w in ws:
        if w.get("kind") == "url" and w.get("enabled") and now - w.get("last_check", 0) >= w["every"] * 60:
            note = await check(w, now)
            changed = True
            if note:
                await push.notify("👁 " + w["name"], note, tag="watch-" + w["id"], force=False)
    if changed:
        await kv.put(KEY, ws)
    from . import autoremember
    await autoremember.check_dates()


async def loop():
    await asyncio.sleep(15)
    while True:
        try:
            await run_due()
        except Exception as e:  # noqa: BLE001
            print("watcher error:", type(e).__name__, e)
        await asyncio.sleep(60)


def start_loop():
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(loop())
