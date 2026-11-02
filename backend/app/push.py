"""Web push: tell the phone when the agent needs approval or has finished while the app isn't on screen."""
import asyncio
import base64
import json
import time

from . import config, kv

_presence = {"visible": False, "t": 0.0}      # the app reports whether it is on screen
_last: dict[str, float] = {}                  # tag -> last send time (de-dupe)
_vapid = {"pem": None, "pub": ""}
AVAILABLE = True
try:
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid
    from pywebpush import WebPushException, webpush
except Exception:  # noqa: BLE001  (dependency missing: feature quietly off)
    AVAILABLE = False


def _pub_of(v) -> str:
    raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


async def init():
    if not AVAILABLE:
        return
    doc = kv.get("vapid")
    if not doc:
        v = Vapid(); v.generate_keys()
        doc = {"pem": v.private_pem().decode()}
        await kv.put("vapid", doc)
    _vapid["pem"] = doc["pem"]
    _vapid["pub"] = _pub_of(Vapid.from_pem(doc["pem"].encode()))


def public_key() -> str:
    return _vapid["pub"]


def set_presence(visible: bool):
    _presence.update(visible=bool(visible), t=time.time())


def app_is_open() -> bool:
    return _presence["visible"] and time.time() - _presence["t"] < 60


def subs() -> dict:
    return kv.get("push_subs", {}) or {}


async def subscribe(sub: dict):
    d = subs()
    d[sub["endpoint"]] = {"sub": sub, "added": time.time()}
    await kv.put("push_subs", d)


async def unsubscribe(endpoint: str):
    d = subs()
    if d.pop(endpoint, None) is not None:
        await kv.put("push_subs", d)


def _send_one(sub: dict, payload: str):
    return webpush(subscription_info=sub, data=payload, vapid_private_key=Vapid.from_pem(_vapid["pem"].encode()),
                   vapid_claims={"sub": "mailto:" + config.GIT_USER_EMAIL}, ttl=3600)


async def notify(title: str, body: str, sid: str = "", tag: str = "", force: bool = False) -> int:
    """Send to every subscribed device unless the app is open on screen. Returns how many were sent."""
    if not AVAILABLE or not _vapid["pem"] or not subs():
        return 0
    if not force and app_is_open():
        return 0
    key = tag or f"{sid}:{title}"
    if not force and time.time() - _last.get(key, 0) < 20:
        return 0
    _last[key] = time.time()
    payload = json.dumps({"title": title[:80], "body": body[:180], "sid": sid, "tag": tag or key})
    sent, dead = 0, []
    for endpoint, rec in list(subs().items()):
        try:
            await asyncio.to_thread(_send_one, rec["sub"], payload)
            sent += 1
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", 0)
            if code in (404, 410):
                dead.append(endpoint)
        except Exception:  # noqa: BLE001
            pass
    if dead:
        d = subs()
        for e in dead:
            d.pop(e, None)
        await kv.put("push_subs", d)
    return sent
