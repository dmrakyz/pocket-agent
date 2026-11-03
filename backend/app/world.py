"""World engine: a small persistent world of characters who keep living while you're away.

Each world has a premise and a cast (persona, goal, memory). On a schedule one model call plays out the next beat for everyone, appends it to a log,
and updates each character's memory. You can step in with a message ("a stranger knocks"), read the log any time, and get a notification when something happens.
The model is pinned per world: if it fails the tick fails, nothing is silently swapped (a different model means different characters).
"""
import asyncio
import json
import re
import time
import uuid

from . import config, kv, llm, push, usage

KEY = "worlds"
LOG_CAP = 300
MEM_CAP = 24
MIN_MINUTES = 10
MAX_PER_DAY = 48
PAUSE_AFTER_FAILS = 3
_task = None
_busy: set = set()

SYSTEM = """You are the WORLD ENGINE: the narrator of a small ongoing story world. You play every character, one beat at a time, and keep them consistent with their persona, goal and memories.
Rules:
- Advance the story by one small beat. Characters act on their own goals; they do not wait for the user.
- Stay in the fiction. Characters never mention being AI, ticks, or this system.
- If the user stepped in (see "Nobody told you" section), that event happens in the world this beat, and characters react to it.
- Keep it grounded in what came before. Do not repeat the last beat. 1 to 4 events, each 1 to 2 sentences.
Reply with ONLY a JSON object, no other text:
{"events":[{"who":"<character name or Narrator>","kind":"say|do|think","text":"..."}],"memory":{"<character name>":"one short line they will remember from this beat"}}"""


def all_worlds() -> list[dict]:
    return kv.get(KEY, []) or []


async def save(ws: list[dict]):
    await kv.put(KEY, ws)


def find(wid: str):
    return next((w for w in all_worlds() if w["id"] == wid), None)


def parse_cast(text: str) -> list[dict]:
    """One character per line: `Name: persona | goal`."""
    out = []
    for line in str(text or "").splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if not line:
            continue
        name, _, rest = line.partition(":")
        if not rest.strip():
            name, rest = line[:30], line
        persona, _, goal = rest.partition("|")
        out.append({"id": uuid.uuid4().hex[:6], "name": name.strip()[:40], "persona": persona.strip()[:500], "goal": goal.strip()[:300], "memory": []})
    return out[:8]


async def create(body: dict) -> dict:
    cast = body.get("cast")
    cast = parse_cast(cast) if isinstance(cast, str) else [
        {"id": uuid.uuid4().hex[:6], "name": str(c.get("name", ""))[:40], "persona": str(c.get("persona", ""))[:500], "goal": str(c.get("goal", ""))[:300], "memory": []}
        for c in (cast or []) if isinstance(c, dict) and c.get("name")][:8]
    if not cast:
        raise ValueError("add at least one character (one per line: Name: who they are | what they want)")
    w = {"id": uuid.uuid4().hex[:8], "name": (str(body.get("name") or "").strip() or "World")[:60], "premise": str(body.get("premise") or "")[:2000],
         "cast": cast, "model": str(body.get("model") or "") or await llm.default_model(),     # pinned at creation
         "minutes": max(MIN_MINUTES, min(int(body.get("minutes") or 60), 60 * 24)), "enabled": True, "notify": body.get("notify", True) is not False,
         "created": time.time(), "last_tick": time.time(), "log": [], "inbox": [], "fails": 0, "error": "", "ticks": 0, "day": "", "day_n": 0}
    w["log"].append({"t": time.time(), "who": "Narrator", "kind": "do", "text": w["premise"] or "The world begins."})
    ws = all_worlds(); ws.append(w); await save(ws)
    return w


def public(w: dict, full: bool = False) -> dict:
    out = {k: v for k, v in w.items() if k not in ("log", "inbox")}
    out["next_tick"] = (w.get("last_tick", 0) + w["minutes"] * 60) if w.get("enabled") else None
    out["running"] = w["id"] in _busy
    out["pending"] = len(w.get("inbox") or [])
    out["log"] = w["log"][-(LOG_CAP if full else 3):]
    return out


def update(w: dict, body: dict):
    if "name" in body: w["name"] = (str(body["name"]).strip() or w["name"])[:60]
    if "premise" in body: w["premise"] = str(body["premise"])[:2000]
    if "minutes" in body: w["minutes"] = max(MIN_MINUTES, min(int(body["minutes"]), 60 * 24))
    if "notify" in body: w["notify"] = bool(body["notify"])
    if "model" in body and body["model"]: w["model"] = str(body["model"])[:120]
    if "enabled" in body:
        w["enabled"] = bool(body["enabled"])
        if w["enabled"]:
            w["fails"], w["error"], w["last_tick"] = 0, "", time.time()
    if "cast" in body and isinstance(body["cast"], str):
        old = {c["name"].lower(): c for c in w["cast"]}
        new = parse_cast(body["cast"])
        for c in new:
            o = old.get(c["name"].lower())
            if o: c["id"], c["memory"] = o["id"], o["memory"]      # keep what they remember
        if new: w["cast"] = new


def _prompt(w: dict) -> str:
    lines = [f"Premise: {w['premise'] or '(none)'}", f"Now: {time.strftime('%A %H:%M', time.localtime())}", "", "## Cast"]
    for c in w["cast"]:
        lines += [f"### {c['name']}", f"Persona: {c['persona'] or 'unspecified'}", f"Wants: {c['goal'] or 'unspecified'}",
                  "Remembers: " + ("; ".join(c["memory"][-8:]) or "nothing yet"), ""]
    lines.append("## Recent story")
    for e in w["log"][-14:]:
        lines.append(f"[{time.strftime('%a %H:%M', time.localtime(e['t']))}] {e['who']} ({e['kind']}): {e['text']}")
    if w.get("inbox"):
        lines += ["", "## Nobody told you, but this just happened (the user stepped in)"] + [f"- {m}" for m in w["inbox"]]
    lines += ["", "Write the next beat."]
    return "\n".join(lines)


def parse_beat(text: str) -> dict:
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S) or re.search(r"(\{.*\})", t, re.S)
    if not m:
        raise ValueError("the model did not return a story beat")
    d = json.loads(m.group(1))
    evs = [{"who": str(e.get("who") or "Narrator")[:40], "kind": e.get("kind") if e.get("kind") in ("say", "do", "think") else "do", "text": str(e.get("text") or "").strip()[:700]}
           for e in (d.get("events") or []) if isinstance(e, dict) and str(e.get("text") or "").strip()][:6]
    if not evs:
        raise ValueError("the beat had no events")
    mem = {str(k): str(v).strip()[:200] for k, v in (d.get("memory") or {}).items() if str(v).strip()} if isinstance(d.get("memory"), dict) else {}
    return {"events": evs, "memory": mem}


async def tick(wid: str, why: str = "schedule") -> dict:
    """Play one beat. Returns {ok, events|error}. Never swaps the pinned model."""
    if wid in _busy:
        return {"ok": False, "error": "already ticking"}
    ws = all_worlds()
    w = next((x for x in ws if x["id"] == wid), None)
    if not w:
        return {"ok": False, "error": "no such world"}
    _busy.add(wid)
    try:
        async def nop(*_a):
            return None
        inbox = list(w.get("inbox") or [])
        try:
            r = await asyncio.wait_for(llm.chat(w["model"], [{"role": "system", "content": SYSTEM}, {"role": "user", "content": _prompt(w)}], [], nop, nop, nop,
                                               thinking=False, max_tokens=1200, fallback=False), 150)
            usage.record(w["model"], (r.get("usage") or {}).get("prompt_tokens", 0), (r.get("usage") or {}).get("completion_tokens", 0))
            beat = parse_beat(r["message"].get("content") or "")
        except Exception as e:  # noqa: BLE001
            w["fails"] = w.get("fails", 0) + 1
            w["error"] = f"{type(e).__name__}: {str(e)[:160]}"
            w["last_tick"] = time.time()
            if w["fails"] >= PAUSE_AFTER_FAILS:
                w["enabled"] = False
                w["error"] += f" (paused after {PAUSE_AFTER_FAILS} failures in a row; model {w['model']} is pinned, so nothing was swapped)"
                if w.get("notify"):
                    await push.notify(f"🌍 {w['name']} paused", w["error"][:200], tag="world-" + wid, world=wid)
            await save(ws)
            return {"ok": False, "error": w["error"]}
        now = time.time()
        for m in inbox:
            w["log"].append({"t": now, "who": "You", "kind": "do", "text": m})
        for e in beat["events"]:
            w["log"].append({"t": now, **e})
        by = {c["name"].lower(): c for c in w["cast"]}
        for name, line in beat["memory"].items():
            c = by.get(name.lower())
            if c: c["memory"] = (c["memory"] + [line])[-MEM_CAP:]
        w["log"] = w["log"][-LOG_CAP:]
        w["inbox"] = [m for m in w.get("inbox", []) if m not in inbox]
        w["last_tick"], w["fails"], w["error"], w["ticks"] = now, 0, "", w.get("ticks", 0) + 1
        day = time.strftime("%Y-%m-%d")
        w["day_n"] = (w.get("day_n", 0) + 1) if w.get("day") == day else 1
        w["day"] = day
        await save(ws)
        if w.get("notify"):
            await push.notify(f"🌍 {w['name']}", "\n".join(f"{e['who']}: {e['text']}" for e in beat["events"]), tag="world-" + wid, world=wid)
            asyncio.create_task(_telegram(w, f"🌍 {w['name']}\n" + "\n".join(f"{e['who']}: {e['text']}" for e in beat["events"])))
        return {"ok": True, "events": beat["events"], "why": why}
    finally:
        _busy.discard(wid)


async def _telegram(w: dict, text: str):
    try:
        from . import telegram
        if telegram.token():
            for cid in telegram.allowed_ids():
                await telegram.send(cid, text[:3500])
    except Exception:  # noqa: BLE001
        pass


async def say(wid: str, text: str) -> bool:
    ws = all_worlds()
    w = next((x for x in ws if x["id"] == wid), None)
    if not w or not text.strip():
        return False
    w.setdefault("inbox", []).append(text.strip()[:500])
    w["inbox"] = w["inbox"][-5:]
    await save(ws)
    return True


def due(w: dict, now: float) -> bool:
    if not w.get("enabled") or w["id"] in _busy:
        return False
    if w.get("day") == time.strftime("%Y-%m-%d") and w.get("day_n", 0) >= MAX_PER_DAY:
        return False
    return now - w.get("last_tick", 0) >= w["minutes"] * 60


async def loop():
    await asyncio.sleep(8)
    while True:
        try:
            now = time.time()
            for w in all_worlds():
                if due(w, now):
                    await tick(w["id"])
        except Exception as e:  # noqa: BLE001
            print("world scheduler error:", type(e).__name__, e)
        await asyncio.sleep(30)


def start_loop():
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(loop())
