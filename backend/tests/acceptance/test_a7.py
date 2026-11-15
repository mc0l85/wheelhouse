"""A-7: terminal limit -> close 1013 with a reason, after accept (real uvicorn).
Also the origin refusal (PRD 4.2): close 1008 with a reason, after accept."""
from __future__ import annotations

from tests.acceptance.conftest import FAKES, open_ws, wait_close


def test_terminal_limit_closes_1013_after_accept(live_server, live_herdr, override_settings, monkeypatch):
    monkeypatch.setenv("FAKE_HERDR_SECONDS", "10")
    override_settings(herdr_bin=str(FAKES / "herdr_echo.py"), max_terminals=1)
    first = open_ws(live_server.url())
    assert b"fake-herdr ready" in wait_close(first, 3, until_bytes=b"fake-herdr ready").data
    second = open_ws(live_server.url())
    out = wait_close(second, 3)
    assert out.code == 1013, f"over the terminal limit: expected 1013, got {out.code} {out.reason!r}"
    assert out.reason.strip(), "the 1013 close must carry a human-readable reason"
    still = wait_close(first, 0.5)
    assert still.code is None, f"the first terminal was closed too: {still.code}"
    first.close()


def test_foreign_origin_closes_1008_after_accept(live_server, live_herdr):
    ws = open_ws(live_server.url(), origin="http://evil.example")
    out = wait_close(ws, 3)
    assert out.code == 1008, f"foreign Origin: expected 1008, got {out.code} {out.reason!r}"
    assert out.reason.strip(), "the 1008 close must carry a human-readable reason"
