"""/api/push — Web Push subscription management (see app/push.py)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Body, HTTPException

from .. import herdr, push
from ..config import settings

router = APIRouter(prefix="/api/push", tags=["push"])


@router.get("/key")
def key() -> dict:
    return {"public_key": push.public_key(), "subscribers": push.count()}


@router.post("/subscribe")
def subscribe(subscription: dict = Body(...)) -> dict:
    try:
        return {"subscribers": push.subscribe(subscription)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/unsubscribe")
def unsubscribe(endpoint: str = Body(..., embed=True)) -> dict:
    return {"subscribers": push.unsubscribe(endpoint)}


TEST = {"title": "herdr web", "body": "Notifications are working ✓", "test": True}


@router.post("/test")
async def test(delay: int = Body(0, embed=True)) -> dict:
    """delay > 0: send later, so the phone can be locked first (iOS may not show
    a banner for the app that is open on screen)."""
    if not settings.enable_writes:           # it rings every subscribed device
        raise herdr.WritesDisabled("sending a test notification needs HERDRWEB_ENABLE_WRITES=1")
    delay = max(0, min(delay, 30))
    if not delay:
        return await asyncio.to_thread(push.send_all, TEST)

    async def later() -> None:
        await asyncio.sleep(delay)
        await asyncio.to_thread(push.send_all, TEST)
    asyncio.get_running_loop().create_task(later())
    return {"scheduled": push.count(), "delay": delay}


@router.post("/receipt")
def receipt(id: str = Body(...), shown: bool = Body(...), error: str = Body("")) -> dict:
    push.receipt(id, shown, error)
    return {"ok": True}


@router.get("/receipts")
def receipts() -> dict:
    return {"receipts": push.receipts()}
