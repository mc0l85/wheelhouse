"""herdr event hub — one subscription on the control socket, fanned out to browsers.

herdr pushes newline-JSON events after `events.subscribe`. Structural kinds
(pane/tab/workspace created/closed/focused, layout) are global; agent status and
scroll are per pane, so the hub resubscribes whenever the pane set changes.
Browsers get a thin "something changed" frame and refetch /api/tree, which is
three socket round-trips; the 4 s poll in the SPA stays as the fallback.

The hub also records when each pane first went blocked, so the UI can show
"blocked for 3m" and sort the inbox oldest-first.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from . import herdr
from .push import notifier

log = logging.getLogger("herdr-web.events")

_GLOBAL = ["workspace.created", "workspace.closed", "workspace.focused", "workspace.renamed",
           "tab.created", "tab.closed", "tab.focused", "tab.renamed",
           "pane.created", "pane.closed", "pane.focused", "pane.exited",
           "pane.agent_detected", "layout.updated"]
# Events that may change the pane set. herdr replays recent history right after
# subscribing (a pane created and closed minutes ago shows up again), so these
# only trigger a fresh pane.list, and a resubscribe only if the set differs.
_PANE_SET = {"pane_created", "pane_closed", "pane_exited"}


class Hub:
    def __init__(self) -> None:
        self.clients: set[asyncio.Queue] = set()
        self.blocked_since: dict[str, float] = {}
        self._task: asyncio.Task | None = None
        self.up = False

    # -- blocked-since bookkeeping (also fed by every /api/tree read) ---------
    def note_status(self, pane_id: str | None, status: str | None) -> None:
        if not pane_id:
            return
        if status == "blocked":
            self.blocked_since.setdefault(pane_id, time.time())
        else:
            self.blocked_since.pop(pane_id, None)

    def note_statuses(self, panes: list[dict]) -> None:
        live = {p.get("pane_id") for p in panes}
        for pid in list(self.blocked_since):
            if pid not in live:
                self.blocked_since.pop(pid, None)
        for p in panes:
            self.note_status(p.get("pane_id"), p.get("agent_status"))

    # -- fan-out ---------------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self.clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.clients.discard(q)

    def _publish(self, frame: dict[str, Any]) -> None:
        for q in list(self.clients):
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:   # slow client: it will refetch anyway
                pass

    # -- the socket loop ---------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        delay = 1.0
        while True:
            try:
                await self._session()
                delay = 1.0                  # clean resubscribe: go again now
            except asyncio.CancelledError:
                raise
            except Exception as e:           # herdr restarting, socket gone, ...
                log.warning("event stream down: %s; retry in %.0fs", e, delay)
                self.up = False
                self._publish({"type": "stream", "up": False})
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def _session(self) -> None:
        panes = (await asyncio.to_thread(herdr._socket_request, "pane.list", {}) or {}).get("panes", [])
        self.note_statuses(panes)
        notifier.seed(panes)
        subscribed = {p["pane_id"] for p in panes}
        subs = [{"type": t} for t in _GLOBAL]
        for p in panes:
            pid = p["pane_id"]
            subs += [{"type": "pane.agent_status_changed", "pane_id": pid},
                     {"type": "pane.scroll_changed", "pane_id": pid}]
        reader, writer = await asyncio.open_unix_connection(herdr.socket_path(), limit=1 << 22)
        try:
            req = {"id": "web:events", "method": "events.subscribe", "params": {"subscriptions": subs}}
            writer.write((json.dumps(req) + "\n").encode())
            await writer.drain()
            self.up = True
            self._publish({"type": "stream", "up": True})
            while True:
                line = await reader.readline()
                if not line:
                    raise ConnectionError("herdr closed the event stream")
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "error" in msg:
                    raise ConnectionError(f"subscribe refused: {msg['error']}")
                ev = msg.get("event")
                if not ev:
                    continue                 # the subscription_started ack
                data = msg.get("data") or {}
                pane_id = data.get("pane_id") or (data.get("pane") or {}).get("pane_id")
                if ev == "pane.agent_status_changed":
                    self.note_status(pane_id, data.get("agent_status"))
                    notifier.on_status(pane_id, data.get("agent_status"))
                frame: dict[str, Any] = {"type": "event", "event": ev, "pane_id": pane_id}
                if ev == "pane.scroll_changed":
                    frame["scroll"] = data.get("scroll")
                if ev == "pane.agent_status_changed":
                    frame["agent_status"] = data.get("agent_status")
                self._publish(frame)
                if ev in _PANE_SET:
                    now = (await asyncio.to_thread(herdr._socket_request, "pane.list", {}) or {}).get("panes", [])
                    if {p["pane_id"] for p in now} != subscribed:
                        return               # rebuild per-pane subscriptions
        finally:
            writer.close()


hub = Hub()
