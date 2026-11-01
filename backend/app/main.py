"""herdr-web FastAPI app — REST + WebSockets over the live herdr session, plus the SPA.

Run (dev):   uvicorn app.main:app --reload --port 8086
Run (prod):  see deploy/install.sh
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import herdr
from .config import settings
from .models import Health
from .events import hub
from .origin import origin_ok
from .routers import events, panes, push, structure, terminal


app = FastAPI(title="herdr-web", version="0.1.0",
              description="A browser wrapper over a herdr terminal workspace server.")

if settings.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_methods=["*"], allow_headers=["*"])
# DNS-rebinding guard: a hostile page re-pointing its own name at this address
# would pass a same-origin check, but not a Host allowlist. Covers WebSockets too.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)


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


@app.websocket("/{path:path}")
async def _ws_unrouted(ws: WebSocket, path: str) -> None:
    """Any other WS path: refuse cleanly instead of falling into StaticFiles."""
    await ws.close(code=1008)

# Serve the built SPA last so /api/* wins. Present only after `vite build`.
if os.path.isdir(settings.static_dir):
    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="spa")
