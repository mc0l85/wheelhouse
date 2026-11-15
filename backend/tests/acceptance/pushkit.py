"""Shared B-1/B-2 harness: a fresh events.Hub on the fake herdr API, with push delivery recorded.

Pinned names: app.events.Hub, app.events.notifier / app.push.notifier (Notifier instance used by
the hub), app.push.Notifier, app.push.CONFIRM_DELAY_S (read at call time), app.push.count,
app.push.send_all (called once per push).
"""
from __future__ import annotations

import asyncio
import time


class PushRig:
    def __init__(self, fake_api, monkeypatch, confirm_s: float):
        from app import events, push
        self.api = fake_api
        self.status = {"w1:p1": "idle"}
        self.sent: list[dict] = []
        fake_api.on("pane.list", handler=lambda p: {"panes": [
            {"pane_id": pid, "tab_id": "w1:t1", "workspace_id": "w1", "agent": "claude",
             "agent_status": st} for pid, st in self.status.items()]})
        fake_api.on("tab.list", result={"tabs": [{"tab_id": "w1:t1", "workspace_id": "w1", "label": "T"}]})
        fake_api.on("workspace.list", result={"workspaces": [{"workspace_id": "w1", "label": "W"}]})
        fresh = push.Notifier()
        monkeypatch.setattr(push, "notifier", fresh)
        monkeypatch.setattr(events, "notifier", fresh)
        monkeypatch.setattr(push, "CONFIRM_DELAY_S", confirm_s)
        monkeypatch.setattr(push, "count", lambda: 1)
        monkeypatch.setattr(push, "send_all", lambda payload: self.sent.append(payload) or
                            {"sent": 1, "dropped": 0, "failed": 0, "id": "t"})
        self.hub = events.Hub()

    def subscribers(self) -> int:
        with self.api._lock:
            return len(self.api._subs)

    async def wait_subscribed(self, n_before: int = 0, timeout: float = 8.0) -> None:
        """Wait until the hub holds a (new) subscription."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.subscribers() > n_before:
                await asyncio.sleep(0.1)          # let the hub finish its post-subscribe work
                return
            await asyncio.sleep(0.02)
        raise AssertionError("the hub never subscribed to the fake herdr event stream")

    async def status_event(self, pane_id: str, status: str) -> None:
        self.status[pane_id] = status
        self.api.push_event("pane.agent_status_changed", {"pane_id": pane_id, "agent_status": status})
        await asyncio.sleep(0)

    def pushes_for(self, pane_id: str) -> int:
        return sum(1 for p in self.sent if p.get("pane_id") == pane_id)
