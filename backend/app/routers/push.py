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


@router.post("/test")
async def test() -> dict:
    if not settings.enable_writes:           # it rings every subscribed device
        raise herdr.WritesDisabled("sending a test notification needs HERDRWEB_ENABLE_WRITES=1")
    return await asyncio.to_thread(push.send_all, {
        "title": "herdr web", "body": "Notifications are working ✓", "test": True})
