"""A-3: herdr server not running -> close 4503 after accept (real uvicorn); the client binary is
never executed; the probe returns within its timeout. Three ways to be "not running": no socket
file, a stale socket file with no listener, a listener that never replies."""
from __future__ import annotations

import os
import socket
import threading
import time

import pytest

from tests.acceptance.conftest import FAKES, open_ws, wait_close

PROBE_S = 1.0


@pytest.fixture
def marker(tmp_path, override_settings, monkeypatch):
    path = tmp_path / "client-executed"
    monkeypatch.setenv("FAKE_HERDR_MARKER", str(path))
    override_settings(herdr_bin=str(FAKES / "herdr_marker.py"), probe_timeout=PROBE_S)
    return path


def _sock_path() -> str:
    from app import herdr
    p = herdr.socket_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if os.path.exists(p):
        os.unlink(p)
    return p


@pytest.fixture
def no_socket():
    p = _sock_path()
    yield p


@pytest.fixture
def stale_socket():
    p = _sock_path()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(p)
    s.close()                                # the file stays; connect() is refused
    assert os.path.exists(p)
    yield p
    if os.path.exists(p):
        os.unlink(p)


@pytest.fixture
def silent_listener():
    """Accepts, reads, never replies."""
    p = _sock_path()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(p)
    srv.listen(16)
    srv.settimeout(0.1)
    stop = threading.Event()
    held: list[socket.socket] = []

    def run():
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            held.append(c)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    yield p
    stop.set()
    srv.close()
    for c in held:
        c.close()
    t.join(2)
    if os.path.exists(p):
        os.unlink(p)


CASES = ["no_socket", "stale_socket", "silent_listener"]


@pytest.mark.parametrize("case", CASES)
def test_attach_closes_4503_and_never_runs_the_client(case, request, live_server, marker):
    request.getfixturevalue(case)
    ws = open_ws(live_server.url())
    out = wait_close(ws, PROBE_S + 2.0)
    assert out.code == 4503, f"{case}: expected close 4503 (herdr server not running), got {out.code} {out.reason!r}"
    assert out.reason.strip(), "the 4503 close must carry a human-readable reason"
    time.sleep(0.5)
    assert not marker.exists(), f"{case}: the herdr client was executed: {marker.read_text()!r}"


def test_probe_runs_off_the_event_loop(live_server, marker, silent_listener):
    """Two attaches against a silent listener: a probe blocking the loop would serialise them."""
    from websockets.sync.client import connect  # noqa: F401
    results: dict[int, object] = {}

    def attach(i):
        ws = open_ws(live_server.url())
        results[i] = wait_close(ws, PROBE_S * 2 + 3)
    t0 = time.monotonic()
    ts = [threading.Thread(target=attach, args=(i,)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(15)
    took = time.monotonic() - t0
    assert all(getattr(results.get(i), "code", None) == 4503 for i in range(2)), results
    assert took < PROBE_S * 2, f"two concurrent probes took {took:.2f}s: the probe blocks the event loop"


@pytest.mark.parametrize("case", CASES)
def test_server_alive_false_within_timeout(case, request, override_settings):
    from app import herdr
    request.getfixturevalue(case)
    override_settings(cli_timeout=30.0)            # the probe must use ITS timeout, not the CLI one
    t0 = time.monotonic()
    assert herdr.server_alive(0.5) is False
    assert time.monotonic() - t0 < 1.0, "server_alive overran its timeout"


def test_server_alive_true_on_any_reply(fake_api):
    """A live server answers; an error reply (unscripted method) still proves it is alive."""
    from app import herdr
    assert herdr.server_alive(1.0) is True
    fake_api.on("pane.list", result={"panes": []})
    assert herdr.server_alive(1.0) is True
