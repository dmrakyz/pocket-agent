"""Token and run counts per day and model (kv `usage`), so you can see what the agent costs even on free tiers."""
import time

from . import kv

KEEP_DAYS = 60
_pending: dict = {}      # {day: {model: {"in","out","calls"}}}
_runs: dict = {}         # {day: n}
_last_flush = 0.0


def _day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def record(model: str, tokens_in: int = 0, tokens_out: int = 0):
    d = _pending.setdefault(_day(), {}).setdefault(model or "unknown", {"in": 0, "out": 0, "calls": 0})
    d["in"] += int(tokens_in or 0); d["out"] += int(tokens_out or 0); d["calls"] += 1


def record_run():
    _runs[_day()] = _runs.get(_day(), 0) + 1


async def flush(force: bool = False):
    """Merge what we counted into the stored doc. Throttled, because every write is a commit to the state repo."""
    global _last_flush
    if not _pending and not _runs:
        return
    if not force and time.time() - _last_flush < 120:
        return
    _last_flush = time.time()
    doc = kv.get("usage", {}) or {}
    days = doc.setdefault("days", {})
    for day, models in _pending.items():
        dd = days.setdefault(day, {"runs": 0, "models": {}})
        for m, v in models.items():
            t = dd["models"].setdefault(m, {"in": 0, "out": 0, "calls": 0})
            for k in ("in", "out", "calls"):
                t[k] += v[k]
    for day, n in _runs.items():
        days.setdefault(day, {"runs": 0, "models": {}})["runs"] += n
    _pending.clear(); _runs.clear()
    for old in sorted(days)[:-KEEP_DAYS]:
        days.pop(old)
    await kv.put("usage", doc)


def view(n: int = 14) -> dict:
    """Stored + not-yet-flushed numbers, newest day first."""
    doc = kv.get("usage", {}) or {}
    days = {d: {"runs": v.get("runs", 0), "models": {m: dict(x) for m, x in (v.get("models") or {}).items()}} for d, v in (doc.get("days") or {}).items()}
    for day, models in _pending.items():
        dd = days.setdefault(day, {"runs": 0, "models": {}})
        for m, v in models.items():
            t = dd["models"].setdefault(m, {"in": 0, "out": 0, "calls": 0})
            for k in ("in", "out", "calls"):
                t[k] += v[k]
    for day, c in _runs.items():
        days.setdefault(day, {"runs": 0, "models": {}})["runs"] += c
    out = []
    for day in sorted(days, reverse=True)[:n]:
        v = days[day]
        out.append({"day": day, "runs": v["runs"], "in": sum(x["in"] for x in v["models"].values()), "out": sum(x["out"] for x in v["models"].values()),
                    "calls": sum(x["calls"] for x in v["models"].values()), "models": v["models"]})
    total = {"in": sum(d["in"] for d in out), "out": sum(d["out"] for d in out), "runs": sum(d["runs"] for d in out), "calls": sum(d["calls"] for d in out)}
    return {"days": out, "total": total}
