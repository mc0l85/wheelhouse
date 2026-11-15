"""/api/debug/input — dev-only input-event trace from the terminal's textarea.

DISABLED unless HERDRWEB_DEBUG_INPUT=1 (main.py does not mount this router otherwise).
Pair with a frontend built with VITE_INPUT_TRACE=1; production builds omit the recorder.

The page POSTs batches of textarea/composition events and what it sent to the pane, so a
phone keyboard's real event sequence (iOS dictation, predictive text) can be read back
with GET. Memory only, last 2000 events; a restart clears it.
"""
from __future__ import annotations

import os
import time
from collections import deque

from fastapi import APIRouter, Body

ENABLED = os.environ.get("HERDRWEB_DEBUG_INPUT", "0") == "1"

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
