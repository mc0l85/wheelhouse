"""Same-origin check for WebSockets and mutating requests.

Browsers always send Origin on a WebSocket handshake and on cross-site POSTs.
A request without one did not come from a browser page, so it cannot be a
cross-site attack and is allowed (curl, scripts on a host that can already
reach the port).
"""
from __future__ import annotations

from urllib.parse import urlsplit

from starlette.datastructures import Headers

from .config import settings


def origin_ok(headers: Headers) -> bool:
    origin = headers.get("origin")
    if not origin:
        return True
    if origin in settings.allowed_origins:
        return True
    # Compare with Host only. X-Forwarded-Host is client-controllable unless a
    # proxy overwrites it; a proxy must pass Host through (docs/NETWORKING.md).
    return urlsplit(origin).netloc.lower() == (headers.get("host") or "").lower()
