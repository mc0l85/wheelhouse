"""A-4: nine spawn failures, then a success still connects (the live-terminal counter is not leaked)."""
from __future__ import annotations

import errno

from tests.acceptance.conftest import FAKES, open_ws, wait_close


def test_nine_spawn_failures_then_success_connects(live_server, live_herdr, override_settings, monkeypatch):
    from app.routers import terminal
    override_settings(herdr_bin=str(FAKES / "herdr_echo.py"), max_terminals=8)
    monkeypatch.setenv("FAKE_HERDR_SECONDS", "10")
    real = terminal._spawn
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] <= 9:
            raise OSError(errno.EAGAIN, "fork failed (A-4 test)")
        return real(*a, **k)
    monkeypatch.setattr(terminal, "_spawn", flaky)

    for i in range(9):
        ws = open_ws(live_server.url())          # every refusal is after accept
        out = wait_close(ws, 3.0)
        assert out.code is not None, f"attach {i + 1}: a failed spawn must close the socket"
    ws = open_ws(live_server.url())
    out = wait_close(ws, 3.0, until_bytes=b"fake-herdr ready")
    assert b"fake-herdr ready" in out.data, (f"the 10th attach must connect after 9 spawn failures; "
                                              f"got close {out.code} {out.reason!r}")
    assert calls["n"] == 10
    ws.close()
