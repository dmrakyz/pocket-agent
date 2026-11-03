"""Backup and restore: one zip with your chats, apps (and their data), routines, worlds, forged tools, memory and connector list.

Secrets never go in: no tokens, passwords, OAuth sign-ins, push subscriptions, Telegram/Instagram pairings, Shortcut/MCP tokens, or connector headers.
Restore is strict: only known file names, every item re-validated, caps on size, nothing overwritten unless asked, and anything that could start running
(routines, worlds, forged tools) comes back PAUSED/OFF so a restore can never start spending or acting by itself.
"""
import io
import json
import re
import time
import uuid
import zipfile

from . import apps, forge, kv, memory, store, tools

VERSION = 1
MAX_ZIP = 60 * 1024 * 1024
MAX_FILE = 20 * 1024 * 1024
MAX_TOTAL = 150 * 1024 * 1024
MAX_FILES = 3000
SID = re.compile(r"^[a-f0-9]{16}$")
APPID = re.compile(r"^[a-z0-9-]{1,40}$")
NAME_OK = re.compile(
    r"^(manifest\.json|sessions/[a-f0-9]{16}\.json|kv/(apps|routines|worlds|forged_tools|memory|connectors|connector_perms)\.json|kv/(app|appdata)_[a-z0-9-]{1,40}\.json)$")


class BackupError(Exception):
    pass


def _j(o) -> bytes:
    return json.dumps(o, ensure_ascii=False).encode("utf-8")


def export_zip() -> tuple[bytes, dict]:
    buf = io.BytesIO()
    counts = {"chats": 0, "apps": 0, "routines": 0, "worlds": 0, "forged tools": 0, "memory notes": 0, "connectors": 0}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for s in list(store.sessions.values()):
            if s.get("_ephemeral"):
                continue
            z.writestr(f"sessions/{s['id']}.json", _j(store._clean(s))); counts["chats"] += 1
        idx = apps.index()
        z.writestr("kv/apps.json", _j(idx))
        for aid in idx:
            if APPID.match(aid):
                z.writestr(f"kv/app_{aid}.json", _j(kv.get("app_" + aid, {}) or {}))
                z.writestr(f"kv/appdata_{aid}.json", _j(apps.data(aid)))
                counts["apps"] += 1
        rs = [{k: v for k, v in r.items() if k != "hook"} for r in (kv.get("routines", []) or [])]
        z.writestr("kv/routines.json", _j(rs)); counts["routines"] = len(rs)
        ws = kv.get("worlds", []) or []
        z.writestr("kv/worlds.json", _j(ws)); counts["worlds"] = len(ws)
        ft = kv.get("forged_tools", {}) or {}
        z.writestr("kv/forged_tools.json", _j(ft)); counts["forged tools"] = len(ft)
        mem = kv.get("memory", []) or []
        z.writestr("kv/memory.json", _j(mem)); counts["memory notes"] = len(mem)
        cons = [{"name": c["name"], "url": c["url"], **({"oauth": True} if c.get("oauth") else {})} for c in (kv.get("connectors", []) or []) if c.get("name") and c.get("url")]
        z.writestr("kv/connectors.json", _j(cons)); counts["connectors"] = len(cons)
        z.writestr("kv/connector_perms.json", _j(kv.get("connector_perms", {}) or {}))
        z.writestr("manifest.json", _j({"app": "pocket-agent", "version": VERSION, "created": int(time.time()), "counts": counts,
                                        "note": "no tokens, passwords, sign-ins or connector headers are included"}))
    return buf.getvalue(), counts


def _open(raw: bytes) -> zipfile.ZipFile:
    if len(raw) > MAX_ZIP:
        raise BackupError(f"that file is over {MAX_ZIP // 1024 // 1024} MB")
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise BackupError("that is not a zip file")
    infos = z.infolist()
    if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_TOTAL or any(i.file_size > MAX_FILE for i in infos):
        raise BackupError("that backup is too large to restore")
    return z


def _read(z: zipfile.ZipFile) -> dict:
    out = {}
    for i in z.infolist():
        if i.is_dir():
            continue
        if not NAME_OK.match(i.filename):
            continue                      # anything unexpected is ignored, never written
        try:
            out[i.filename] = json.loads(z.read(i).decode("utf-8"))
        except Exception:  # noqa: BLE001
            raise BackupError(f"{i.filename} is damaged")
    if "manifest.json" not in out or out["manifest.json"].get("app") != "pocket-agent":
        raise BackupError("this is not a Pocket Agent backup")
    if int(out["manifest.json"].get("version") or 0) > VERSION:
        raise BackupError("this backup is from a newer version of Pocket Agent; update first")
    return out


def inspect(raw: bytes) -> dict:
    data = _read(_open(raw))
    sess = [k for k in data if k.startswith("sessions/")]
    have = sum(1 for k in sess if k.split("/")[1][:-5] in store.sessions)
    return {"created": data["manifest.json"].get("created"), "counts": data["manifest.json"].get("counts") or {},
            "chats_already_here": have, "apps_already_here": sum(1 for a in (data.get("kv/apps.json") or {}) if a in apps.index())}


def _clip(v, n):
    return str(v if v is not None else "")[:n]


async def restore(raw: bytes, overwrite: bool = False) -> dict:
    data = _read(_open(raw))
    done = {"chats": 0, "apps": 0, "routines": 0, "worlds": 0, "forged tools": 0, "memory notes": 0, "connectors": 0}
    skipped: list[str] = []

    # chats
    for name, s in data.items():
        if not name.startswith("sessions/"):
            continue
        sid = name.split("/")[1][:-5]
        if not isinstance(s, dict) or s.get("id") != sid or not SID.match(sid) or not isinstance(s.get("messages"), list):
            skipped.append(f"chat {sid}: unreadable"); continue
        if sid in store.sessions and not overwrite:
            continue
        s = {k: v for k, v in s.items() if not str(k).startswith("_")}
        s["mode"] = s.get("mode") if s.get("mode") in ("ask", "auto", "plan") else "ask"      # never come back as YOLO
        s["allow"] = []                                                                      # saved "always allow" rules do not travel
        s.setdefault("todos", []); s.setdefault("usage", {"in": 0, "out": 0}); s.setdefault("title", "Restored chat")
        s["title"] = _clip(s["title"], 80)
        store.sessions[sid] = s
        store._dirty[sid] = time.time()
        done["chats"] += 1
    if done["chats"]:
        store._ensure_flusher()

    # apps (+ their data)
    idx = data.get("kv/apps.json") or {}
    if isinstance(idx, dict):
        for aid, meta in idx.items():
            if not APPID.match(str(aid)) or not isinstance(meta, dict):
                continue
            if aid in apps.index() and not overwrite:
                continue
            html = ((data.get(f"kv/app_{aid}.json") or {}).get("html")) if isinstance(data.get(f"kv/app_{aid}.json"), dict) else None
            try:
                await apps.save({"id": aid, "name": meta.get("name"), "icon": meta.get("icon"), "description": meta.get("description"), "html": html}, restore=True)
            except apps.AppError as e:
                skipped.append(f"app {aid}: {e}"); continue
            d = data.get(f"kv/appdata_{aid}.json")
            if isinstance(d, dict) and len(json.dumps(d)) <= apps.MAX_DATA:
                await kv.put("appdata_" + aid, {str(k)[:100]: v for k, v in d.items()})
            done["apps"] += 1

    # routines: paused, never more than Ask
    from . import routines
    have = routines.all_routines()
    for r in (data.get("kv/routines.json") or []):
        if not isinstance(r, dict) or not str(r.get("prompt") or "").strip() or len(have) >= 30:
            continue
        if any(x["name"] == r.get("name") and x.get("prompt") == r.get("prompt") for x in have):
            continue
        mode = r.get("mode") if r.get("mode") in ("ask", "plan") else "ask"
        body = {"name": r.get("name") or "Routine", "prompt": r.get("prompt"), "schedule": r.get("schedule") or {"kind": "none"}, "tz": r.get("tz") or "UTC",
                "mode": mode, "model": r.get("model") or "", "chat": r.get("chat"), "enabled": False}
        try:
            have.append(routines.clean(body)); done["routines"] += 1
        except Exception:  # noqa: BLE001
            skipped.append(f"routine {r.get('name')}: unreadable")
    if done["routines"]:
        await routines.save(have)

    # worlds: paused, fresh clock
    from . import world
    ws = world.all_worlds()
    for w in (data.get("kv/worlds.json") or []):
        if not isinstance(w, dict) or not isinstance(w.get("cast"), list) or len(ws) >= 10:
            continue
        if any(x["name"] == w.get("name") and x.get("premise") == w.get("premise") for x in ws):
            continue
        cast = [{"id": _clip(c.get("id") or uuid.uuid4().hex[:6], 12), "name": _clip(c.get("name"), 40), "persona": _clip(c.get("persona"), 500), "goal": _clip(c.get("goal"), 300),
                 "memory": [_clip(m, 200) for m in (c.get("memory") or [])][-world.MEM_CAP:]} for c in w["cast"] if isinstance(c, dict) and c.get("name")][:8]
        if not cast:
            continue
        log = [{"t": float(e.get("t") or time.time()), "who": _clip(e.get("who"), 40), "kind": e.get("kind") if e.get("kind") in ("say", "do", "think", "look") else "do", "text": _clip(e.get("text"), 700)}
               for e in (w.get("log") or []) if isinstance(e, dict)][-world.LOG_CAP:]
        ws.append({"id": uuid.uuid4().hex[:8], "name": _clip(w.get("name") or "World", 60), "premise": _clip(w.get("premise"), 2000), "cast": cast, "model": _clip(w.get("model"), 120),
                   "minutes": max(world.MIN_MINUTES, min(int(w.get("minutes") or 60), 1440)), "tools": bool(w.get("tools")), "enabled": False, "notify": w.get("notify", True) is not False, "created": time.time(),
                   "last_tick": time.time(), "log": log, "inbox": [], "fails": 0, "error": "", "ticks": int(w.get("ticks") or 0), "day": "", "day_n": 0})
        done["worlds"] += 1
    if done["worlds"]:
        await world.save(ws)

    # forged tools: re-tested in the sandbox, then left switched OFF
    ft = data.get("kv/forged_tools.json") or {}
    for name, spec in (ft.items() if isinstance(ft, dict) else []):
        if not isinstance(spec, dict) or (forge.get(name) and not overwrite):
            continue
        try:
            await forge.add(spec)
            await forge.set_enabled(forge.validate(spec)["name"], False)
            done["forged tools"] += 1
        except Exception as e:  # noqa: BLE001
            skipped.append(f"forged tool {name}: {str(e)[:100]}")

    # memory: through the normal filter (secrets and duplicates are dropped)
    notes = [str(n.get("text") or "") for n in (data.get("kv/memory.json") or []) if isinstance(n, dict)]
    if notes:
        r = await memory.save(notes, source="restore")
        done["memory notes"] = len(r["added"])

    # connectors: name + url only, public https only, no secrets; OAuth ones need signing in again
    cons = kv.get("connectors", []) or []
    for c in (data.get("kv/connectors.json") or []):
        if not isinstance(c, dict) or any(x.get("name") == c.get("name") for x in cons) or len(cons) >= tools.MAX_CONNECTORS:
            continue
        name = re.sub(r"[^a-z0-9_-]", "", str(c.get("name", "")).lower())[:24]
        url = str(c.get("url", "")).strip()
        try:
            if not name or not url.startswith("https://"):
                raise tools.ToolError("bad url")
            tools._check_public(url)
        except Exception:  # noqa: BLE001
            skipped.append(f"connector {c.get('name')}: not a public https address"); continue
        cons.append({"name": name, "url": url, **({"oauth": True} if c.get("oauth") else {})}); done["connectors"] += 1
    if done["connectors"]:
        await kv.put("connectors", cons)
    perms = data.get("kv/connector_perms.json")
    if isinstance(perms, dict) and perms and not (kv.get("connector_perms", {}) or {}):
        await kv.put("connector_perms", {str(k)[:24]: v for k, v in perms.items() if v in ("ask", "readonly", "trusted")})
    return {"restored": done, "skipped": skipped[:20]}
