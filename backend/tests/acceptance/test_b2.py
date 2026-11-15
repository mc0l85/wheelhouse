"""B-2: at most one pending confirm per pane.
blocked -> working -> blocked inside the confirm window -> one push;
blocked -> working and stays -> zero; two episodes outside the window -> two."""
from __future__ import annotations

import asyncio

from tests.acceptance.pushkit import PushRig

CONFIRM = 0.5


def _run(coro):
    asyncio.run(asyncio.wait_for(coro, 30))


def test_flicker_inside_confirm_window_pushes_once(fake_api, monkeypatch):
    rig = PushRig(fake_api, monkeypatch, confirm_s=CONFIRM)

    async def go():
        rig.hub.start()
        await rig.wait_subscribed()
        await rig.status_event("w1:p1", "blocked")
        await asyncio.sleep(0.1)
        await rig.status_event("w1:p1", "working")
        await asyncio.sleep(0.1)
        await rig.status_event("w1:p1", "blocked")
        await asyncio.sleep(CONFIRM + 1.0)
        await rig.hub.stop()
    _run(go())
    assert rig.pushes_for("w1:p1") == 1, rig.sent


def test_blocked_then_working_and_stays_pushes_zero(fake_api, monkeypatch):
    rig = PushRig(fake_api, monkeypatch, confirm_s=CONFIRM)

    async def go():
        rig.hub.start()
        await rig.wait_subscribed()
        await rig.status_event("w1:p1", "blocked")
        await asyncio.sleep(0.1)
        await rig.status_event("w1:p1", "working")
        await asyncio.sleep(CONFIRM + 1.0)
        await rig.hub.stop()
    _run(go())
    assert rig.pushes_for("w1:p1") == 0, rig.sent


def test_two_episodes_outside_the_window_push_twice(fake_api, monkeypatch):
    rig = PushRig(fake_api, monkeypatch, confirm_s=CONFIRM)

    async def go():
        rig.hub.start()
        await rig.wait_subscribed()
        await rig.status_event("w1:p1", "blocked")
        await asyncio.sleep(CONFIRM + 0.4)
        await rig.status_event("w1:p1", "working")
        await asyncio.sleep(0.2)
        await rig.status_event("w1:p1", "blocked")
        await asyncio.sleep(CONFIRM + 0.6)
        await rig.hub.stop()
    _run(go())
    assert rig.pushes_for("w1:p1") == 2, rig.sent
