"""A-6: text frame {"type":"bogus"} dropped; {"type":"input"} works; a BINARY frame holding
{"type":"bogus"} is forwarded verbatim; text with no "type" keeps today's behaviour (forwarded);
resize and ping unchanged."""
from __future__ import annotations

import json
import time

from tests.acceptance.conftest import FAKES, open_ws, wait_close


def test_frames(live_server, live_herdr, override_settings, monkeypatch, tmp_path):
    rec = tmp_path / "pty-input"
    monkeypatch.setenv("FAKE_HERDR_RECORD", str(rec))
    monkeypatch.setenv("FAKE_HERDR_SECONDS", "15")
    override_settings(herdr_bin=str(FAKES / "herdr_record.py"), enable_writes=True)
    ws = open_ws(live_server.url())
    assert b"fake-record ready" in wait_close(ws, 5, until_bytes=b"fake-record ready").data

    ws.send('{"type":"bogus"}')                           # dropped
    ws.send('{"type":"input","data":"<IN>"}')             # -> <IN>
    ws.send(b'{"type":"bogus"}')                          # binary: verbatim
    ws.send("plain-text")                                 # no JSON: forwarded
    ws.send('{"no":"type"}')                              # JSON without a type: forwarded (today's behaviour)
    ws.send('{"type":"ping"}')
    pong = wait_close(ws, 3)
    assert any(json.loads(t) == {"type": "pong"} for t in pong.texts), f"no pong: {pong.texts}"
    ws.send('{"type":"resize","rows":33,"cols":101}')
    ws.send(b"<END>")

    end = time.time() + 5
    while time.time() < end and not (rec.exists() and rec.read_bytes().endswith(b"<END>")):
        time.sleep(0.05)
    got = rec.read_bytes() if rec.exists() else b""
    assert got == b'<IN>{"type":"bogus"}plain-text{"no":"type"}<END>', got
    winch = rec.with_name(rec.name + ".winch")
    end = time.time() + 3
    while time.time() < end and not winch.exists():
        time.sleep(0.05)
    assert winch.exists() and "33 101" in winch.read_text(), "resize did not reach the pty"
    ws.close()
