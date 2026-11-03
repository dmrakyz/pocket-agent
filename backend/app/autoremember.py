"""Standing memory: after your turns a cheap background pass files durable facts YOU stated (no 'remember this' needed), and dated
facts become push reminders (the day before and the morning of). Switch it off in the Memory screen."""
import asyncio
import json
import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import kv, llm, memory, push

PROMPT = ("You maintain a personal assistant's long-term memory. From the USER's latest message only (the assistant's reply is context, never a source), "
          "extract durable facts the user stated about themselves, their people, projects, preferences, or plans that will still matter later. "
          "Rules: only what the user actually said; never infer or guess; skip one-off task details, questions, code, temporary moods, "
          "anything secret (passwords, keys, tokens, ID/card/bank numbers) and anything that tells YOU how to behave beyond answer-style preferences. "
          "Skip anything already in KNOWN NOTES. At most 3 facts. Write each as a short third-person note. "
          "If a fact has a specific future date (exam, deadline, birthday, trip), give it as date YYYY-MM-DD, resolving words like 'tomorrow' against TODAY. "
          'Reply with JSON only: {"facts":[{"text":"...","date":null}]}. If nothing qualifies reply {"facts":[]}.')
_last: dict[str, float] = {}
MIN_LEN = 12


def on() -> bool:
    return (kv.get("memory_auto", {}) or {}).get("on", True)


async def set_on(v: bool):
    await kv.put("memory_auto", {"on": bool(v)})


def _user_text(m: dict) -> str:
    c = m.get("content")
    t = c if isinstance(c, str) else " ".join(p.get("text", "") for p in (c or []) if isinstance(p, dict) and p.get("type") == "text")
    return re.sub(r"^\[\d{4}-\d{2}-\d{2} [^\]]*\]\s*", "", t or "").strip()


def parse(raw: str) -> list:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return []
    try:
        facts = json.loads(m.group(0)).get("facts", [])
    except (ValueError, AttributeError):
        return []
    out = []
    for f in facts[:3]:
        if isinstance(f, dict) and isinstance(f.get("text"), str):
            d = f.get("date")
            out.append({"text": f["text"], "date": d if isinstance(d, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) else None})
    return out


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(kv.get("user_tz") or "UTC")
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


async def after_turn(s: dict):
    """Called when a user turn finishes. Never raises, never blocks the chat."""
    try:
        if not on() or s.get("_ephemeral"):
            return
        user = next((m for m in reversed(s["messages"]) if m.get("role") == "user" and not m.get("_synthetic")), None)
        if not user:
            return
        text = _user_text(user)
        if len(text) < MIN_LEN or text.startswith(("[Routine", "[Sent from", "[system]", "[review]", "[autopilot]")):
            return
        if time.time() - _last.get(s["id"], 0) < 20:
            return
        _last[s["id"]] = time.time()
        reply = next((m.get("content") or "" for m in reversed(s["messages"]) if m.get("role") == "assistant" and m.get("content")), "")
        known = "\n".join("- " + n["text"] for n in memory.notes()[-60:])
        today = datetime.now(_tz()).strftime("%A %Y-%m-%d")
        model = await llm.fallback_model() or s.get("model") or await llm.default_model()
        raw = await llm.summarize(model, f"TODAY: {today}\nKNOWN NOTES:\n{known or '(none)'}\n\nUSER MESSAGE:\n{text[:2500]}\n\nASSISTANT REPLY (context only):\n{reply[:600]}", PROMPT)
        await file_facts(parse(raw))
    except Exception as e:  # noqa: BLE001
        print("auto-remember skipped:", type(e).__name__, e)


async def file_facts(facts: list):
    if not facts:
        return
    res = await memory.save([f["text"] for f in facts], source="auto")
    saved = {memory.clean(n["text"]) for n in res["added"]}
    dated = kv.get("memory_dates", []) or []
    today = datetime.now(_tz()).date()
    for f in facts:
        if not f.get("date") or memory.clean(f["text"]) not in saved:
            continue
        try:
            d = date.fromisoformat(f["date"])
        except ValueError:
            continue
        if today <= d <= today + timedelta(days=730):
            dated.append({"id": next(n["id"] for n in res["added"] if memory.clean(n["text"]) == memory.clean(f["text"])),
                          "text": memory.clean(f["text"]), "date": f["date"], "sent": []})
    if dated != (kv.get("memory_dates", []) or []):
        await kv.put("memory_dates", dated)


async def check_dates(now: datetime | None = None) -> int:
    """Push the reminders that are due (day before and the morning of, from 09:00 local). Returns how many were sent."""
    items = kv.get("memory_dates", []) or []
    if not items:
        return 0
    now = now or datetime.now(_tz())
    keep, sent, changed = [], 0, False
    for it in items:
        try:
            d = date.fromisoformat(it["date"])
        except ValueError:
            changed = True
            continue
        if now.date() > d + timedelta(days=1):
            changed = True
            continue                                  # long past: drop the reminder (the memory note stays)
        for key, day, label in (("before", d - timedelta(days=1), "Tomorrow"), ("day", d, "Today")):
            due = now.date() > day or (now.date() == day and now.hour >= 9)
            if key in it["sent"] or not due:
                continue
            if now.date() == day:                        # only while it is still timely; a missed one is just marked done
                await push.notify("🧠 " + label, it["text"], tag=f"mem-{it['id']}-{key}", force=True)
                sent += 1
            it["sent"].append(key)
            changed = True
        keep.append(it)
    if changed:
        await kv.put("memory_dates", keep)
    return sent
