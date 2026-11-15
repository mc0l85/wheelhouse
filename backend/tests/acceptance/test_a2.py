"""A-2: herdr client exit -> close 1012 within 2 s, including while output is backed up."""
from __future__ import annotations

import time

from tests.acceptance.conftest import FAKES, RawWS, open_ws, raw_wait_close, wait_close, wait_file


def test_client_exit_closes_1012_within_2s(live_server, live_herdr, override_settings):
    override_settings(herdr_bin=str(FAKES / "herdr_exit.py"))
    ws = open_ws(live_server.url())
    out = wait_close(ws, 2.0)
    assert out.code == 1012, f"client exit must close 1012 within 2 s, got {out.code} {out.reason!r}"


def test_client_exit_while_output_backed_up_closes_1012(live_server, live_herdr, override_settings, monkeypatch,
                                                        tmp_path):
    """The client writes 24 MiB and exits while the browser is not reading. The backlog cap is
    raised far above that so this tests exit detection, not the 1013 backlog close (A-5). The
    consumer is a RawWS, so not reading really backs the server up (see A-5)."""
    from app.routers import terminal
    monkeypatch.setattr(terminal, "OUT_Q_MAX_BYTES", 256 << 20)
    monkeypatch.setenv("FAKE_HERDR_FLOOD_BYTES", str(24 << 20))
    monkeypatch.setenv("FAKE_HERDR_SECONDS", "0")
    monkeypatch.setenv("FAKE_HERDR_STARTED", str(tmp_path / "started"))
    override_settings(herdr_bin=str(FAKES / "herdr_flood.py"))
    ws = RawWS(live_server.port)
    wait_file(tmp_path / "started")
    time.sleep(2.5)                      # not reading: the flood is written and the client has exited
    ws.unthrottle()
    out = raw_wait_close(ws, 2.0)
    assert out.code == 1012, (f"client exit with output backed up must close 1012 within 2 s of the "
                              f"browser reading again, got {out.code} {out.reason!r} after {len(out.data)} bytes")
