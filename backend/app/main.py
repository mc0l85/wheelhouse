"""herdr-web FastAPI app — REST + WebSockets over the live herdr session, plus the SPA.

Run (dev):   uvicorn app.main:app --reload --port 8086
Run (prod):  see deploy/install.sh
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import Receive, Scope, Send
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import herdr
from .config import settings
from .models import Health
from .events import hub
from .origin import origin_ok
from .routers import debug, events, panes, push, structure, terminal, upload


app = FastAPI(title="herdr-web", version="0.1.0",
              description="A browser wrapper over a herdr terminal workspace server.",
              docs_url=None, redoc_url=None, openapi_url=None)

if settings.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_methods=["*"], allow_headers=["*"])
# DNS-rebinding guard: a hostile page re-pointing its own name at this address
# would pass a same-origin check, but not a Host allowlist. Covers WebSockets too.
def host_only(value: str) -> str:
    """Hostname from a Host header, lowercased: `name`, `name:port`, `[v6]`, `[v6]:port`.
    Starlette's own parsing splits on the first ':' and mangles bracketed IPv6.
    A malformed value (junk after the closing bracket) comes back unchanged, so it
    matches nothing."""
    v = value.strip().lower()
    if v.startswith("["):
        end = v.find("]")
        if end == -1:
            return v
        rest = v[end + 1:]
        if rest == "" or (rest[0] == ":" and rest[1:].isdigit()):
            return v[1:end]
        return v
    return v.split(":")[0]


class HostGuard:
    """DNS-rebinding guard (Host allowlist) for HTTP and WebSocket. Entries may be
    names, IPs, bracketed IPv6, `*.suffix` wildcards, or `*` for any."""

    def __init__(self, app) -> None:
        self.app = app
        hosts = [h.strip().lower() for h in settings.allowed_hosts]
        hosts = [h[1:-1] if h.startswith("[") and h.endswith("]") else h for h in hosts]
        self.any_host = "*" in hosts
        self.exact = set(hosts)
        self.suffixes = tuple(h[1:] for h in hosts if h.startswith("*."))

    def ok(self, host: str) -> bool:
        return host in self.exact or (bool(host) and host.endswith(self.suffixes) if self.suffixes else False)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or self.any_host:
            return await self.app(scope, receive, send)
        if self.ok(host_only(Headers(scope=scope).get("host", ""))):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await PlainTextResponse("Invalid host header", status_code=400)(scope, receive, send)


app.add_middleware(HostGuard)


@app.middleware("http")
async def _origin_gate(request: Request, call_next):
    """Cross-site request forgery guard: a mutating /api call must come from this
    app's own page (or an allowed origin). Reads stay open; there is no login."""
    if (request.method not in ("GET", "HEAD", "OPTIONS")
            and request.url.path.startswith("/api") and not origin_ok(request.headers)):
        return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def _cache_policy(request: Request, call_next):
    """The page, service worker and manifest must revalidate, or a home-screen app
    keeps running last week's build. Hashed /assets/* files never change."""
    resp = await call_next(request)
    path = request.url.path
    if path.startswith("/assets/"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif not path.startswith("/api"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.on_event("startup")
async def _start_events() -> None:
    hub.start()


@app.on_event("shutdown")
async def _stop_events() -> None:
    await hub.stop()


@app.exception_handler(herdr.WritesDisabled)
async def _writes_disabled(_: Request, exc: herdr.WritesDisabled):
    return JSONResponse({"detail": str(exc)}, status_code=403)


@app.exception_handler(herdr.BadRequest)
async def _bad_request(_: Request, exc: herdr.BadRequest):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(herdr.HerdrError)
async def _herdr_error(_: Request, exc: herdr.HerdrError):
    return JSONResponse({"detail": str(exc), "stderr": exc.stderr}, status_code=502)


@app.get("/api/health", response_model=Health)
def health() -> Health:
    try:
        ps = herdr.panes()
        return Health(ok=True, herdr_bin=os.path.basename(settings.herdr_bin),
                      session=settings.herdr_session, server_running=True,
                      writes_enabled=settings.enable_writes, pane_count=len(ps))
    except herdr.HerdrError as e:
        return Health(ok=False, herdr_bin=os.path.basename(settings.herdr_bin),
                      session=settings.herdr_session, server_running=False,
                      writes_enabled=settings.enable_writes, error=str(e))


app.include_router(structure.router)
app.include_router(panes.router)
app.include_router(terminal.router)
app.include_router(events.router)
app.include_router(push.router)
app.include_router(upload.router)
if debug.ENABLED:   # dev-only input trace; off unless HERDRWEB_DEBUG_INPUT=1
    app.include_router(debug.router)


@app.websocket("/{path:path}")
async def _ws_unrouted(ws: WebSocket, path: str) -> None:
    """Any other WS path: refuse cleanly instead of falling into StaticFiles."""
    await ws.close(code=1008)

# Serve the built SPA last so /api/* wins. Present only after `vite build`.
if os.path.isdir(settings.static_dir):
    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="spa")
