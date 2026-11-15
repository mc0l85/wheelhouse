"""A-5: multi-MB output to a slow consumer arrives byte-exact and in order, or the socket closes
1013 "output backlog" -- never silent loss; the backlog close happens only above OUT_Q_MAX_BYTES."""
from __future__ import annotations

import time

from tests.acceptance.conftest import FAKES, open_ws, wait_close
from tests.fakes.herdr_flood import pattern


def _flood(monkeypatch, override_settings, n: int, cap: int):
    from app.routers import terminal
    monkeypatch.setattr(terminal, "OUT_Q_MAX_BYTES", cap)
    monkeypatch.setenv("FAKE_HERDR_FLOOD_BYTES", str(n))
    monkeypatch.setenv("FAKE_HERDR_SECONDS", "15")      # stays alive: no exit close in the way
    override_settings(herdr_bin=str(FAKES / "herdr_flood.py"))


def test_out_q_max_bytes_is_a_sane_cap():
    from app.routers import terminal
    assert isinstance(terminal.OUT_Q_MAX_BYTES, int) and terminal.OUT_Q_MAX_BYTES >= 1 << 20


def test_slow_consumer_below_cap_gets_every_byte_in_order(live_server, live_herdr, override_settings, monkeypatch):
    n = 6 << 20
    _flood(monkeypatch, override_settings, n, cap=64 << 20)
    ws = open_ws(live_server.url(), max_queue=2)
    time.sleep(2.0)                                       # stalled consumer
    out = wait_close(ws, 30.0, until_bytes=pattern(n)[-256:], pause=0.002)   # then slow
    assert out.code is None, f"closed {out.code} {out.reason!r} below the cap"
    assert len(out.data) == n, f"got {len(out.data)} of {n} bytes"
    assert out.data == pattern(n), "bytes arrived altered or out of order"
    ws.close()


def test_backlog_over_cap_closes_1013_and_loses_nothing_silently(live_server, live_herdr, override_settings, monkeypatch):
    n = 32 << 20
    _flood(monkeypatch, override_settings, n, cap=1 << 20)
    ws = open_ws(live_server.url(), max_queue=2)
    time.sleep(3.0)                                       # the browser stops reading
    out = wait_close(ws, 30.0)
    assert out.code == 1013, f"a backlog over the cap must close 1013, got {out.code} {out.reason!r}"
    assert "backlog" in out.reason.lower(), f"close reason must say 'backlog': {out.reason!r}"
    assert len(out.data) < n, "everything arrived: the backlog close never happened"
    assert out.data == pattern(n)[:len(out.data)], "what did arrive is not an exact in-order prefix"
