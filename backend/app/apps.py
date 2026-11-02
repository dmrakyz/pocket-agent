"""App drawer: mini-apps the agent builds. Each is ONE self-contained HTML page kept in the state repo (kv), shown on a grid in the PWA.

Apps run in a sandboxed frame with an opaque origin and a CSP that blocks all network, so they can't phone home or touch the app. They get a tiny SDK (window.agent):
ask(prompt) = one-shot answer from the agent's model, get/set/del/keys = their own saved data. The host page relays those calls; see main.py (/api/apps/...)."""
import json
import re
import secrets
import time

from . import kv

MAX_HTML = 200_000
MAX_APPS = 40
MAX_DATA = 100_000
MAX_ASK_CHARS = 8000
ASKS_PER_HOUR = 40
_asks: dict[str, list[float]] = {}

CSP = ("sandbox allow-scripts allow-forms allow-modals; default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; "
       "img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; base-uri 'none'; form-action 'none'")

SDK = """<script>(function(){var P={},n=0;function call(t,d){return new Promise(function(res,rej){var id=++n;P[id]={res:res,rej:rej};
parent.postMessage(Object.assign({__pa:1,id:id,type:t},d||{}),'*');setTimeout(function(){if(P[id]){delete P[id];rej(new Error('timed out'))}},120000)})}
addEventListener('message',function(e){var m=e.data;if(!m||!m.__pa_r)return;var p=P[m.id];if(!p)return;delete P[m.id];m.error?p.rej(new Error(m.error)):p.res(m.value)});
window.agent={ask:function(q,o){return call('ask',{prompt:String(q),system:o&&o.system?String(o.system):''})},get:function(k){return call('get',{key:String(k)})},
set:function(k,v){return call('set',{key:String(k),value:v})},del:function(k){return call('del',{key:String(k)})},keys:function(){return call('keys')}}})();</script>"""


class AppError(Exception):
    pass


def index() -> dict:
    return kv.get("apps", {}) or {}


def get(app_id: str) -> dict | None:
    return index().get(app_id)


def html_of(app_id: str) -> str:
    return (kv.get("app_" + app_id, {}) or {}).get("html", "")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "app"


def public(a: dict) -> dict:
    return {k: a.get(k) for k in ("id", "name", "icon", "description", "created", "updated", "ai", "size")}


def with_sdk(html: str) -> str:
    m = re.search(r"<head[^>]*>", html, re.I)
    return (html[:m.end()] + SDK + html[m.end():]) if m else (SDK + html)


async def save(spec: dict) -> dict:
    name = str(spec.get("name") or "").strip()[:40]
    html = str(spec.get("html") or "")
    if not name:
        raise AppError("name is required")
    if "<" not in html or len(html) < 40:
        raise AppError("html must be a complete page (inline CSS and JS in one file)")
    if len(html.encode()) > MAX_HTML:
        raise AppError(f"html is over {MAX_HTML // 1000} KB; make it smaller")
    d = index()
    app_id = str(spec.get("id") or "").strip()
    if app_id and app_id not in d:
        raise AppError(f"no app with id {app_id!r}; omit id to create a new one")
    if not app_id:
        if len(d) >= MAX_APPS:
            raise AppError(f"you already have {MAX_APPS} apps; delete one first")
        app_id = slug(name)
        while app_id in d:
            app_id = slug(name) + "-" + secrets.token_hex(2)
    old = d.get(app_id) or {}
    d[app_id] = {"id": app_id, "name": name, "icon": (str(spec.get("icon") or old.get("icon") or "▦").strip() or "▦")[:4],
                 "description": str(spec.get("description") or old.get("description") or "")[:200], "created": old.get("created") or int(time.time()),
                 "updated": int(time.time()), "ai": "agent.ask" in html, "size": len(html)}
    await kv.put("app_" + app_id, {"html": html})
    await kv.put("apps", d)
    return d[app_id]


async def rename(app_id: str, name: str = "", icon: str = "") -> dict | None:
    d = index()
    if app_id not in d:
        return None
    if name.strip():
        d[app_id]["name"] = name.strip()[:40]
    if icon.strip():
        d[app_id]["icon"] = icon.strip()[:4]
    await kv.put("apps", d)
    return d[app_id]


async def delete(app_id: str) -> bool:
    d = index()
    if app_id not in d:
        return False
    d.pop(app_id)
    await kv.put("apps", d)
    await kv.put("app_" + app_id, {})
    await kv.put("appdata_" + app_id, {})
    return True


def ask_ok(app_id: str) -> bool:
    now = time.time()
    hits = [t for t in _asks.get(app_id, []) if now - t < 3600]
    if len(hits) >= ASKS_PER_HOUR:
        _asks[app_id] = hits
        return False
    hits.append(now)
    _asks[app_id] = hits
    return True


# ---- per-app saved data (small JSON values)
def data(app_id: str) -> dict:
    return kv.get("appdata_" + app_id, {}) or {}


async def data_set(app_id: str, key: str, value) -> None:
    if not key or len(key) > 80:
        raise AppError("key must be 1-80 characters")
    d = data(app_id)
    d[key] = value
    if len(json.dumps(d)) > MAX_DATA:
        raise AppError(f"app data is over {MAX_DATA // 1000} KB")
    await kv.put("appdata_" + app_id, d)


async def data_del(app_id: str, key: str) -> None:
    d = data(app_id)
    if d.pop(key, None) is not None:
        await kv.put("appdata_" + app_id, d)
