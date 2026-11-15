"""/api/debug/input — temporary input-event trace from the terminal's textarea (2026-11-15).

The page POSTs batches of textarea/composition events and what it sent to the pane, so a
phone keyboard's real event sequence (iOS dictation, predictive text) can be read back
with GET. Memory only, last 2000 events; a restart clears it.
"""
from __future__ import annotations

import time
from collections import deque

from fastapi import APIRouter, Body

router = APIRouter(prefix="/api/debug", tags=["debug"])

_EVENTS: deque = deque(maxlen=2000)


@router.post("/input")
def post_input(events: list = Body(..., embed=True)) -> dict:
    now = time.time()
    for e in events[:400]:
        if isinstance(e, dict):
            _EVENTS.append({"recv": round(now, 3), **{k: e[k] for k in list(e)[:12]}})
    return {"stored": len(_EVENTS)}


@router.get("/input")
def get_input(clear: bool = False) -> dict:
    out = list(_EVENTS)
    if clear:
        _EVENTS.clear()
    return {"events": out}
