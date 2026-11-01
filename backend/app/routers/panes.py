"""Pane endpoints — read content + the gated drive surface (send/run/split…)."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .. import herdr
from ..models import ActionResult, PaneRead

router = APIRouter(prefix="/api/panes", tags=["panes"])


@router.get("/{pane_id}/read", response_model=PaneRead)
def read(pane_id: str, source: str = "visible", lines: int = 200,
         format: str = "ansi") -> PaneRead:
    lines = max(1, min(lines, 5000))
    content = herdr.pane_read(pane_id, source=source, lines=lines, fmt=format)
    return PaneRead(pane_id=pane_id, source=source, format=format,
                    content=content, lines=lines)


# --- gated mutations ---------------------------------------------------------
class SendText(BaseModel):
    text: str


class SendKeys(BaseModel):
    keys: list[str]


class RunCmd(BaseModel):
    command: str


class Split(BaseModel):
    direction: str            # "right" | "down"
    cwd: str | None = None
    focus: bool = False


class Rename(BaseModel):
    label: str


@router.post("/{pane_id}/send-text", response_model=ActionResult)
def send_text(pane_id: str, body: SendText) -> ActionResult:
    return ActionResult(ok=True, detail=herdr.pane_send_text(pane_id, body.text))


@router.post("/{pane_id}/send-keys", response_model=ActionResult)
def send_keys(pane_id: str, body: SendKeys) -> ActionResult:
    return ActionResult(ok=True, detail=herdr.pane_send_keys(pane_id, body.keys))


@router.post("/{pane_id}/run", response_model=ActionResult)
def run(pane_id: str, body: RunCmd) -> ActionResult:
    return ActionResult(ok=True, detail=herdr.pane_run(pane_id, body.command))


@router.post("/{pane_id}/split", response_model=ActionResult)
def split(pane_id: str, body: Split) -> ActionResult:
    return ActionResult(ok=True, detail=herdr.pane_split(
        pane_id, body.direction, body.cwd, body.focus))


@router.post("/{pane_id}/rename", response_model=ActionResult)
def rename(pane_id: str, body: Rename) -> ActionResult:
    return ActionResult(ok=True, detail=herdr.pane_rename(pane_id, body.label))


@router.post("/{pane_id}/focus", response_model=ActionResult)
def focus(pane_id: str) -> ActionResult:
    return ActionResult(ok=True, detail=herdr.pane_focus(pane_id))


class Zoom(BaseModel):
    mode: str = "toggle"      # "toggle" | "on" | "off"


@router.post("/{pane_id}/zoom", response_model=ActionResult)
def zoom(pane_id: str, body: Zoom) -> ActionResult:
    return ActionResult(ok=True, detail=herdr.pane_zoom(pane_id, body.mode))


@router.post("/{pane_id}/close", response_model=ActionResult)
def close(pane_id: str) -> ActionResult:
    return ActionResult(ok=True, detail=herdr.pane_close(pane_id))
