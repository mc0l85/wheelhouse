"""Web Push: tell a closed app that an agent is blocked.

The browser (an installed home-screen app on iOS 16.4+, any modern desktop or
Android browser) subscribes with our VAPID public key; we keep the
subscriptions in a small JSON file and POST encrypted payloads to each
browser's push service (Apple, Google, Mozilla). Only outbound HTTPS is
needed, so this works on a LAN-only deployment.

State lives in HERDRWEB_STATE_DIR (default ~/.local/state/herdr-web):
  vapid_private.pem   generated on first use, mode 0600
  push_subs.json      one entry per subscribed browser, mode 0600
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from . import herdr
from .config import settings

log = logging.getLogger("herdr-web.push")

# herdr replays recent events right after subscribing, so a "blocked" event
# may be history. Wait, then re-read the pane and only notify if it is
# STILL blocked — this also swallows a blocked→working flicker.
CONFIRM_DELAY_S = 2.0

_lock = threading.Lock()


def _state() -> Path:
    d = Path(settings.state_dir).expanduser()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def _write_private(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _key_path() -> Path:
    p = _state() / "vapid_private.pem"
    with _lock:
        if not p.exists():
            key = ec.generate_private_key(ec.SECP256R1())
            _write_private(p, key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()))
    return p


def public_key() -> str:
    """applicationServerKey for pushManager.subscribe(): base64url, no padding."""
    key = serialization.load_pem_private_key(_key_path().read_bytes(), password=None)
    raw = key.public_key().public_bytes(serialization.Encoding.X962,
                                        serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _subs_path() -> Path:
    return _state() / "push_subs.json"


def _load() -> list[dict]:
    try:
        return json.loads(_subs_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(subs: list[dict]) -> None:
    _write_private(_subs_path(), json.dumps(subs, indent=1).encode())


# The browsers' push services. Anything else would let a client make this
# server POST to an arbitrary URL (an internal address, say).
PUSH_HOST_SUFFIXES = (
    ".push.apple.com",            # Safari / iOS / macOS
    "fcm.googleapis.com",         # Chrome, Edge on Android, most Chromium
    ".push.services.mozilla.com",  # Firefox
    ".notify.windows.com",        # Edge on Windows
)


def endpoint_ok(endpoint: str) -> bool:
    u = urlsplit(endpoint)
    if u.netloc in settings.push_extra_hosts or u.hostname in settings.push_extra_hosts:
        return True
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(
        host == s.lstrip(".") or host.endswith(s if s.startswith(".") else "." + s)
        for s in PUSH_HOST_SUFFIXES)


def subscribe(sub: dict) -> int:
    ep, keys = sub.get("endpoint"), sub.get("keys")
    if not isinstance(ep, str) or not isinstance(keys, dict) or \
            not all(isinstance(keys.get(k), str) for k in ("p256dh", "auth")):
        raise ValueError("not a PushSubscription")
    if not endpoint_ok(ep):
        raise ValueError("push endpoint is not a known browser push service")
    with _lock:
        subs = [s for s in _load() if s.get("endpoint") != ep]
        if len(subs) >= settings.push_max_subs:
            subs = subs[-(settings.push_max_subs - 1):]       # drop the oldest
        subs.append({"endpoint": ep, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]},
                     "added": time.time()})
        _save(subs)
        return len(subs)


def unsubscribe(endpoint: str) -> int:
    with _lock:
        subs = [s for s in _load() if s.get("endpoint") != endpoint]
        _save(subs)
        return len(subs)


def count() -> int:
    return len(_load())


def send_all(payload: dict) -> dict:
    """Blocking. Returns {"sent": n, "dropped": n, "failed": n}."""
    from pywebpush import WebPushException, webpush   # heavy import, only when used

    key = str(_key_path())
    sent = dropped = failed = 0
    gone: list[str] = []
    for s in _load():
        if not endpoint_ok(s["endpoint"]):      # settings may have tightened
            continue
        try:
            webpush({"endpoint": s["endpoint"], "keys": s["keys"]}, json.dumps(payload),
                    vapid_private_key=key, vapid_claims={"sub": settings.push_contact},
                    ttl=3600, headers={"Urgency": "high"}, timeout=10)
            sent += 1
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):          # the browser unsubscribed or the app was removed
                gone.append(s["endpoint"])
                dropped += 1
            else:
                failed += 1
                log.warning("push to %s failed: %s", s["endpoint"][:40], e)
        except Exception as e:                # network trouble: keep the subscription
            failed += 1
            log.warning("push to %s failed: %s", s["endpoint"][:40], e)
    for ep in gone:
        unsubscribe(ep)
    return {"sent": sent, "dropped": dropped, "failed": failed}


def _describe(pane_id: str) -> tuple[bool, str, int]:
    """(still blocked?, "tab · agent" label, total blocked panes) from the live socket."""
    tabs = {t.get("tab_id"): t for t in (herdr._socket_request("tab.list", {}) or {}).get("tabs", [])}
    panes = (herdr._socket_request("pane.list", {}) or {}).get("panes", [])
    blocked = [p for p in panes if p.get("agent_status") == "blocked"]
    me = next((p for p in panes if p.get("pane_id") == pane_id), None)
    if not me or me.get("agent_status") != "blocked":
        return False, "", len(blocked)
    who = me.get("agent") or (me.get("terminal_title_stripped") or "")[:28] or "shell"
    tab = (tabs.get(me.get("tab_id")) or {}).get("label") or ""
    return True, f"{tab} · {who}" if tab else who, len(blocked)


class Notifier:
    """Fed by the event hub: one push per pane per blocked episode."""

    def __init__(self) -> None:
        self.notified: set[str] = set()

    def seed(self, panes: list[dict]) -> None:
        # Already blocked when the stream (re)starts: never announce those.
        self.notified |= {p["pane_id"] for p in panes if p.get("agent_status") == "blocked"}

    def on_status(self, pane_id: str | None, status: str | None) -> None:
        if not pane_id:
            return
        if status != "blocked":
            self.notified.discard(pane_id)
            return
        if pane_id in self.notified or not count():
            return
        self.notified.add(pane_id)
        asyncio.get_running_loop().create_task(self._confirm_and_send(pane_id))

    async def _confirm_and_send(self, pane_id: str) -> None:
        await asyncio.sleep(CONFIRM_DELAY_S)
        try:
            still, label, n = await asyncio.to_thread(_describe, pane_id)
        except Exception as e:
            log.warning("push: could not re-read %s: %s", pane_id, e)
            return
        if not still:
            self.notified.discard(pane_id)
            return
        body = "An agent is waiting for you" if settings.push_private else f"{label} needs you"
        payload = {"title": "Agent blocked", "body": body, "pane_id": pane_id, "blocked": n}
        r = await asyncio.to_thread(send_all, payload)
        log.info("push for %s: %s", pane_id, r)


notifier = Notifier()
