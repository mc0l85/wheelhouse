"""Pydantic response models — the herdr-web API contract.

These mirror the herdr CLI JSON shapes but are
defensive: herdr fields that may be absent are Optional so a herdr version bump
never 500s the wrapper.
"""
from __future__ import annotations

from pydantic import BaseModel


class AgentSession(BaseModel):
    agent: str | None = None
    kind: str | None = None        # e.g. "id"
    source: str | None = None      # e.g. "herdr:claude"
    value: str | None = None       # the agent's own session id (claude/agy uuid)


class Pane(BaseModel):
    pane_id: str
    terminal_id: str | None = None
    tab_id: str | None = None
    workspace_id: str | None = None
    agent: str | None = None
    agent_status: str | None = None        # idle|working|blocked|unknown
    agent_session: AgentSession | None = None
    cwd: str | None = None
    foreground_cwd: str | None = None
    focused: bool = False
    revision: int | None = None


class PaneRead(BaseModel):
    pane_id: str
    source: str
    format: str                            # "ansi" | "text"
    content: str
    lines: int


class ActionResult(BaseModel):
    ok: bool
    detail: dict | str | None = None


class Health(BaseModel):
    ok: bool
    herdr_bin: str
    session: str
    server_running: bool
    writes_enabled: bool
    pane_count: int | None = None
    error: str | None = None
