"""App drawer: mini-apps the agent builds. Each is ONE self-contained HTML page kept in the state repo (kv), shown on a grid in the PWA.

Apps run in a sandboxed frame with an opaque origin and a CSP that blocks all network, so they can't phone home or touch the app. They get a tiny SDK (window.agent):
ask(prompt) = one-shot answer from the agent's model, act(prompt) = the real agent does something for the app with its connectors (Notion, mail, ...) once the user has allowed it, get/set/del/keys = their own saved data. The host page relays those calls; see main.py (/api/apps/...)."""
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
ACTS_PER_HOUR = 20
_asks: dict[str, list[float]] = {}

CSP = ("sandbox allow-scripts allow-forms allow-modals; default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; "
       "img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; base-uri 'none'; form-action 'none'")

SDK = """<script>(function(){var P={},n=0;function call(t,d){return new Promise(function(res,rej){var id=++n;P[id]={res:res,rej:rej};
parent.postMessage(Object.assign({__pa:1,id:id,type:t},d||{}),'*');setTimeout(function(){if(P[id]){delete P[id];rej(new Error('timed out'))}},120000)})}
addEventListener('message',function(e){var m=e.data;if(!m||!m.__pa_r)return;var p=P[m.id];if(!p)return;delete P[m.id];m.error?p.rej(new Error(m.error)):p.res(m.value)});
window.agent={ask:function(q,o){return call('ask',{prompt:String(q),system:o&&o.system?String(o.system):''})},act:function(q){return call('act',{prompt:String(q)})},get:function(k){return call('get',{key:String(k)})},
set:function(k,v){return call('set',{key:String(k),value:v})},del:function(k){return call('del',{key:String(k)})},keys:function(){return call('keys')}};
/* localStorage/sessionStorage throw in an opaque-origin sandbox; give apps working ones. localStorage is kept in the app's saved data (keys 'ls:...'), sessionStorage lives until the app closes. */
function store(persist){var D=persist?(window.__PA_DATA||{}):{},M={};function ks(){return Object.keys(D).filter(function(k){return !persist||k.indexOf('ls:')===0})}
function nm(k){return persist?'ls:'+k:k}
M.getItem=function(k){k=nm(String(k));return k in D&&D[k]!==null?String(D[k]):null};
M.setItem=function(k,v){k=nm(String(k));v=String(v);D[k]=v;if(persist)call('set',{key:k,value:v}).catch(function(){})};
M.removeItem=function(k){k=nm(String(k));delete D[k];if(persist)call('del',{key:k}).catch(function(){})};
M.clear=function(){ks().forEach(function(k){delete D[k];if(persist)call('del',{key:k}).catch(function(){})})};
M.key=function(i){var a=ks();return i<a.length?(persist?a[i].slice(3):a[i]):null};
return new Proxy(M,{get:function(t,p){if(p==='length')return ks().length;if(p in t)return t[p];return typeof p==='string'?t.getItem(p):undefined},
set:function(t,p,v){t.setItem(p,v);return true},deleteProperty:function(t,p){t.removeItem(p);return true},has:function(t,p){return p in t||t.getItem(String(p))!==null}})}
['localStorage','sessionStorage'].forEach(function(n,i){try{Object.defineProperty(window,n,{value:store(i===0),configurable:true})}catch(e){}})})();</script>"""


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
    return {k: a.get(k) for k in ("id", "name", "icon", "description", "created", "updated", "ai", "size", "act", "granted")}


def with_sdk(html: str, app_id: str = "") -> str:
    # the app's saved data goes in the page itself so localStorage can be read synchronously
    seed = json.dumps(data(app_id) if app_id else {}).replace("<", "\\u003c")
    sdk = "<script>window.__PA_DATA=" + seed + ";</script>" + SDK
    m = re.search(r"<head[^>]*>", html, re.I)
    return (html[:m.end()] + sdk + html[m.end():]) if m else (sdk + html)


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
    same = bool(old) and (kv.get("app_" + app_id, {}) or {}).get("html") == html
    d[app_id] = {"id": app_id, "name": name, "icon": (str(spec.get("icon") or old.get("icon") or "▦").strip() or "▦")[:4],
                 "description": str(spec.get("description") or old.get("description") or "")[:200], "created": old.get("created") or int(time.time()),
                 "updated": int(time.time()), "ai": "agent.ask" in html or "agent.act" in html, "size": len(html),
                 "act": "agent.act" in html, "granted": bool(old.get("granted")) and same and "agent.act" in html}   # changed code must be re-approved
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


def ask_ok(app_id: str, kind: str = "ask") -> bool:
    now, key, cap = time.time(), app_id + ":" + kind, (ACTS_PER_HOUR if kind == "act" else ASKS_PER_HOUR)
    hits = [t for t in _asks.get(key, []) if now - t < 3600]
    if len(hits) >= cap:
        _asks[key] = hits
        return False
    hits.append(now)
    _asks[key] = hits
    return True


async def grant(app_id: str, allow: bool) -> dict | None:
    d = index()
    if app_id not in d:
        return None
    d[app_id]["granted"] = bool(allow)
    await kv.put("apps", d)
    return d[app_id]


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
