"""WS /api/ws/events — herdr's pushed events, relayed to the SPA."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..events import hub
from ..origin import origin_ok

router = APIRouter(prefix="/api/ws", tags=["events"])


@router.websocket("/events")
async def events(ws: WebSocket) -> None:
    if not origin_ok(ws.headers):           # same cross-site guard as terminal.py
        await ws.close(code=1008)
        return
    await ws.accept()
    q = hub.subscribe()
    await ws.send_json({"type": "stream", "up": hub.up})   # hello: is herdr's stream live?

    async def drain_client() -> None:      # notice the browser leaving
        while True:
            if (await ws.receive()).get("type") == "websocket.disconnect":
                return

    closer = asyncio.create_task(drain_client())
    try:
        while not closer.done():
            getter = asyncio.create_task(q.get())
            done, _ = await asyncio.wait({getter, closer}, return_when=asyncio.FIRST_COMPLETED)
            if getter not in done:
                getter.cancel()
                break
            await ws.send_json(getter.result())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        closer.cancel()
        hub.unsubscribe(q)
