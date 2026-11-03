"""Standing memory: extraction from the user's own words, dated reminders, off switch, no secrets."""
import asyncio, json, os, shutil, sys, time
from datetime import datetime
from zoneinfo import ZoneInfo
shutil.rmtree("/tmp/agent-auto", ignore_errors=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.update({"APP_PASSWORD": "pw", "DATA_DIR": "/tmp/agent-auto"})
from app import autoremember as A, kv, memory, push, llm
sent = []
async def fake_notify(title, body, **kw): sent.append((title, body, kw)); return 1
push.notify = fake_notify
fails = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: fails.append(n)
CALLS = []
REPLY = {"v": '{"facts":[]}'}
async def fake_summarize(model, text, instruction):
    CALLS.append(text); return REPLY["v"]
llm.summarize = fake_summarize
async def _fb(): return "m"
llm.fallback_model = _fb
def sess(text, reply="ok", **kw):
    return {"id": "s" + str(len(CALLS)) + str(time.time()), "model": "m", "messages": [{"role": "user", "content": text}, {"role": "assistant", "content": reply}], **kw}

async def main():
    await kv.put("user_tz", "Europe/Istanbul")
    tz = ZoneInfo("Europe/Istanbul"); today = datetime.now(tz).date()
    check("parse accepts fenced/loose JSON, caps at 3, validates the date", len(A.parse('```json\n{"facts":[{"text":"a","date":"2026-12-03"},{"text":"b","date":"soon"},{"text":"c"},{"text":"d"}]}\n```')) == 3 and A.parse('{"facts":[{"text":"a","date":"soon"}]}')[0]["date"] is None and A.parse("garbage") == [])
    REPLY["v"] = json.dumps({"facts": [{"text": "Is learning Rust", "date": None}]})
    await A.after_turn(sess("by the way I started learning Rust last week"))
    check("a stated fact is filed without being asked, tagged auto", [n for n in memory.notes() if n["text"] == "Is learning Rust" and n["source"] == "auto"])
    check("the model is told today's date and the known notes", "TODAY:" in CALLS[-1] and "KNOWN NOTES" in CALLS[-1])
    n = len(CALLS)
    await A.after_turn(sess("ok"))
    check("very short messages cost no model call", len(CALLS) == n)
    await A.after_turn(sess("[Routine 'x' started by schedule at now] check my mail please"))
    check("routine and system messages are never mined", len(CALLS) == n)
    await A.after_turn(sess("my api key is sk-abcdefghijklmnopqrstuvwxyz123456 remember it", _ephemeral=True))
    check("ephemeral (eval) chats are never mined", len(CALLS) == n)
    REPLY["v"] = json.dumps({"facts": [{"text": "api key is sk-abcdefghijklmnopqrstuvwxyz123456", "date": None}, {"text": "Prefers answers in Turkish", "date": None}]})
    await A.after_turn(sess("here is my key sk-abcdefghijklmnopqrstuvwxyz123456 and answer in Turkish from now"))
    texts = [x["text"] for x in memory.notes()]
    check("secrets are refused, the rest is kept", "Prefers answers in Turkish" in texts and not any("sk-" in t for t in texts), str(texts))
    await A.after_turn(sess("by the way I started learning Rust last week again"))
    check("duplicates are not filed twice", sum(1 for x in memory.notes() if x["text"] == "Is learning Rust") == 1)
    # dated reminders
    d = today.replace(year=today.year) 
    from datetime import timedelta
    exam = (today + timedelta(days=2)).isoformat()
    REPLY["v"] = json.dumps({"facts": [{"text": "Has an aerodynamics exam", "date": exam}, {"text": "Old thing", "date": (today - timedelta(days=5)).isoformat()}]})
    await A.after_turn(sess("I have my aerodynamics exam in two days, and also something old"))
    dates = kv.get("memory_dates")
    check("a future date becomes a reminder; a past date doesn't", len(dates) == 1 and dates[0]["date"] == exam, str(dates))
    sent.clear()
    D = datetime.fromisoformat(exam)
    at = lambda days, h: datetime(today.year, today.month, today.day, h, 0, tzinfo=tz) + timedelta(days=days)
    check("nothing before the morning of the day before", await A.check_dates(at(0, 15)) == 0 and not sent)
    check("'Tomorrow' goes out from 09:00 the day before", await A.check_dates(at(1, 8)) == 0 and await A.check_dates(at(1, 9)) == 1 and sent[-1][0] == "🧠 Tomorrow" and sent[-1][2].get("force"))
    check("and only once", await A.check_dates(at(1, 18)) == 0)
    check("'Today' goes out the morning of", await A.check_dates(at(2, 9)) == 1 and sent[-1][0] == "🧠 Today" and sent[-1][1] == "Has an aerodynamics exam")
    await A.check_dates(at(4, 9))
    check("finished reminders are dropped, the memory note stays", kv.get("memory_dates") == [] and any("exam" in x["text"] for x in memory.notes()))
    # missed
    REPLY["v"] = json.dumps({"facts": [{"text": "Dentist visit", "date": (today + timedelta(days=1)).isoformat()}]})
    await A.after_turn(sess("dentist tomorrow, don't forget"))
    sent.clear()
    await A.check_dates(at(3, 12))
    check("a reminder the server slept through is not sent late", not sent)
    # off switch
    await A.set_on(False); n = len(CALLS)
    await A.after_turn(sess("I now live in Bursa and work on exoskeletons"))
    check("the off switch stops all mining", len(CALLS) == n and not A.on())
    await A.set_on(True)
    check("it is on by default and can be turned back on", A.on())
asyncio.run(main())
print("\nALL PASSED" if not fails else "\nFAILED: " + str(fails)); sys.exit(1 if fails else 0)
