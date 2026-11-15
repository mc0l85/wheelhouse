"""Structure endpoints — the read-only view of the live herdr session."""
from __future__ import annotations

from fastapi import APIRouter

from .. import herdr

router = APIRouter(prefix="/api", tags=["structure"])


@router.get("/workspaces")
def workspaces() -> dict:
    """Full workspace→tab→pane tree from herdr's session.json (authoritative)."""
    return herdr.workspaces_from_session()


@router.get("/tree")
def tree() -> dict:
    """Live workspace->tab->pane tree (herdr order) with per-pane agent status."""
    return herdr.tree()


@router.get("/layout/{pane_id}")
def layout(pane_id: str) -> dict:
    """Cell rects of every pane in this pane's tab (for aiming wheel scrolls)."""
    return (herdr._socket_request("pane.layout", {"pane_id": herdr.pid(pane_id)}) or {}).get("layout", {})


@router.get("/panes")
def panes() -> dict:
    """Flat pane list with agent + status, straight from `herdr pane list`."""
    return {"panes": herdr.panes()}


@router.get("/panes/{pane_id}")
def pane_get(pane_id: str) -> dict:
    return herdr.pane_get(pane_id)


@router.get("/agents")
def agents() -> dict:
    return {"agents": herdr.agents()}


@router.get("/tabs")
def tabs(workspace_id: str | None = None) -> dict:
    return {"tabs": herdr.tabs(workspace_id)}


@router.get("/worktrees")
def worktrees(workspace_id: str | None = None, cwd: str | None = None) -> dict:
    return {"worktrees": herdr.worktrees(workspace_id, cwd)}

