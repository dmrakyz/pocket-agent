"""A sense of time for the model: every real user message, earlier answer and tool result it sees carries a timestamp in the user's time zone.

The stored chat is untouched (the UI has its own clock); stamps are added to a copy of the history just before each model call.
The model is told never to write such a stamp itself, and one that does anyway is stripped from its reply."""
import re
import time
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from . import kv

LEAD = re.compile(r"^\s*\[\d{4}-\d{2}-\d{2}[^\]\n]{0,40}\]\s*")


def valid_tz(name) -> bool:
    if not name or not isinstance(name, str) or len(name) > 64 or ZoneInfo is None:
        return False
    try:
        ZoneInfo(name)
        return True
    except Exception:  # noqa: BLE001
        return False


def tz_name(s: dict | None) -> str:
    for cand in ((s or {}).get("tz"), kv.get("user_tz")):
        if valid_tz(cand):
            return cand
    return "UTC"


def _local(ts: float, tz: str) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(ZoneInfo(tz) if ZoneInfo and valid_tz(tz) else timezone.utc)


def stamp(ts: float, tz: str) -> str:
    return _local(ts, tz).strftime("%Y-%m-%d %H:%M")


def clock(ts: float, tz: str) -> str:
    return _local(ts, tz).strftime("%H:%M:%S")


def now_line(s: dict | None) -> str:
    tz = tz_name(s)
    d = _local(time.time(), tz)
    return f"{d.strftime('%A %Y-%m-%d %H:%M')} ({tz}, UTC{d.strftime('%z')[:3]}:{d.strftime('%z')[3:]})"


def ago(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    if s < 172800:
        return f"{s / 3600:.1f}h"
    return f"{s // 86400}d"


def strip_lead(text: str) -> str:
    """Remove a timestamp the model copied onto the front of its own reply."""
    prev = None
    while text != prev:
        prev, text = text, LEAD.sub("", text, count=1)
    return text


NOTE = ("Time: user messages, your earlier replies and tool results carry system-added timestamps like [2026-11-03 13:55] (your replies) or [13:55:08 · 3s] (tool results: "
        "finish time and how long the call took), in the user's time zone. Use them to reason about elapsed time, deadlines, 'how long ago' and what is stale. "
        "Never start your own reply with such a timestamp.")


def _prefix_content(c, pre: str):
    if isinstance(c, str):
        return pre + c
    if isinstance(c, list):
        out, done = [], False
        for p in c:
            if not done and isinstance(p, dict) and p.get("type") == "text":
                out.append({**p, "text": pre + p.get("text", "")})
                done = True
            else:
                out.append(p)
        return out if done else [{"type": "text", "text": pre.strip()}] + out
    return c


def apply(s: dict, msgs: list) -> list:
    """Copy of the history with timestamps added. Synthetic system nudges are left alone."""
    tz = tz_name(s)
    out, last_asst = [], None
    for m in msgs:
        ts = m.get("_ts")
        role = m.get("role")
        if not ts or m.get("_synthetic") or role not in ("user", "assistant", "tool"):
            out.append(m)
            if role == "assistant" and ts:
                last_asst = ts
            continue
        m2 = dict(m)
        if role == "user":
            m2["content"] = _prefix_content(m.get("content"), f"[{stamp(ts, tz)}] ")
        elif role == "assistant":
            last_asst = ts
            if isinstance(m.get("content"), str) and m["content"].strip():
                m2["content"] = f"[{stamp(ts, tz)}] " + m["content"]
        else:
            took = ts - last_asst if last_asst else 0
            tag = f"[{clock(ts, tz)}" + (f" · {ago(took)}" if took >= 2 else "") + "]"
            if isinstance(m.get("content"), str):
                m2["content"] = m["content"] + "\n" + tag
        out.append(m2)
    return out
