"""B-1: a pane idle before a resubscribe, blocked in pane.list at the resubscribe, with no
status event -> exactly one push (reconcile on every (re)subscribe)."""
from __future__ import annotations

import asyncio

from tests.acceptance.pushkit import PushRig


def _run(coro):
    asyncio.run(asyncio.wait_for(coro, 30))


def test_blocked_at_resubscribe_without_event_pushes_once(fake_api, monkeypatch):
    rig = PushRig(fake_api, monkeypatch, confirm_s=0.2)

    async def go():
        rig.hub.start()
        await rig.wait_subscribed()
        assert rig.sent == []
        rig.status["w1:p1"] = "blocked"          # changes while the stream is down; no event
        fake_api.drop_subscribers()
        await rig.wait_subscribed()
        await asyncio.sleep(1.5)
        await rig.hub.stop()
    _run(go())
    assert rig.pushes_for("w1:p1") == 1, rig.sent


def test_replayed_blocked_event_after_resubscribe_still_pushes_once(fake_api, monkeypatch):
    rig = PushRig(fake_api, monkeypatch, confirm_s=0.3)

    async def go():
        rig.hub.start()
        await rig.wait_subscribed()
        rig.status["w1:p1"] = "blocked"
        fake_api.drop_subscribers()
        await rig.wait_subscribed()
        await rig.status_event("w1:p1", "blocked")   # herdr replays recent history
        await asyncio.sleep(1.5)
        await rig.hub.stop()
    _run(go())
    assert rig.pushes_for("w1:p1") == 1, rig.sent


def test_already_notified_and_still_blocked_is_not_pushed_again(fake_api, monkeypatch):
    rig = PushRig(fake_api, monkeypatch, confirm_s=0.2)

    async def go():
        rig.hub.start()
        await rig.wait_subscribed()
        await rig.status_event("w1:p1", "blocked")
        await asyncio.sleep(0.8)
        assert rig.pushes_for("w1:p1") == 1, rig.sent
        fake_api.drop_subscribers()               # still blocked across the resubscribe
        await rig.wait_subscribed()
        await asyncio.sleep(1.0)
        await rig.hub.stop()
    _run(go())
    assert rig.pushes_for("w1:p1") == 1, rig.sent
