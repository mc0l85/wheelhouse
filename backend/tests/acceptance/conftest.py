"""Acceptance-test harness (P1). Locked: builders make these tests pass, never edit them.

Close codes are asserted through a REAL uvicorn server (in a thread, 127.0.0.1, random
port) and the `websockets` client library, so a pre-accept close (which a browser sees
as HTTP 403 -> 1006 with no reason) fails the test as an InvalidStatus handshake error.

Interfaces these tests pin (beyond the existing app):
  app.routers.terminal._spawn(session, rows, cols) -> (pid, fd)   forks a herdr client
      UNCONDITIONALLY (no existence check inside): the herdr-binary check (4500) and the socket
      probe (4503) live in the route, before _spawn is called. A-1 calls _spawn directly with a
      binary execvp cannot run and expects a forked child that exits 127.
  app.routers.terminal.reset_live_count()   sets the live-terminal counter to 0 (tests only)
  app.routers.terminal.OUT_Q_MAX_BYTES   output backlog cap, read at call time (tests monkeypatch it)
  app.herdr.server_alive(timeout) -> bool   bounded probe of herdr's socket; any newline-
                                            terminated JSON reply (result OR error) = alive
  app.config.settings.probe_timeout      seconds, the attach-time socket probe timeout
  WS /api/ws/terminal close codes        1008 origin, 1013 limit / "output backlog",
                                         1012 client exited, 4500 binary missing,
                                         4503 herdr server not running -- all AFTER accept
"""
from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field

import pytest
import uvicorn
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

from tests.conftest import FAKES  # noqa: F401  (env is forced by the parent conftest)


def _ensure_loopback() -> None:
    """prd_check runs checks in an empty network namespace, where `lo` exists but is down (bind works,
    connect fails). Bring it up there; fail loudly if that is not possible."""
    import fcntl
    import struct
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:   # SIOCGIFFLAGS: this namespace's lo
        flags = struct.unpack("H", fcntl.ioctl(s.fileno(), 0x8913, struct.pack("256s", b"lo"))[16:18])[0]
    if flags & 0x1:                                  # IFF_UP
        return
    r = subprocess.run(["ip", "link", "set", "lo", "up"], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"loopback is down and `ip link set lo up` failed (rc={r.returncode}): {r.stderr.strip()}")


class LiveServer:
    def __init__(self) -> None:
        _ensure_loopback()
        from app.main import app
        cfg = uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning",
                             timeout_graceful_shutdown=2)
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> "LiveServer":
        self.thread.start()
        end = time.time() + 10
        while not self.server.started:
            if time.time() > end or not self.thread.is_alive():
                raise RuntimeError("uvicorn did not start")
            time.sleep(0.02)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        return self

    def url(self, path: str = "/api/ws/terminal?rows=24&cols=80") -> str:
        return f"ws://127.0.0.1:{self.port}{path}"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(10)


@pytest.fixture
def live_server():
    from app.routers import terminal
    terminal.reset_live_count()            # pinned accessor: each test starts with no live terminals
    srv = LiveServer().start()
    yield srv
    srv.stop()


@dataclass
class Outcome:
    data: bytes = b""
    texts: list[str] = field(default_factory=list)
    code: int | None = None          # None = still open when the wait ended
    reason: str = ""
    closed_after: float | None = None  # seconds from the start of the wait


def open_ws(url: str, **kw):
    """Connect; a pre-accept close is a test FAILURE (the browser would see 403 -> 1006, no reason)."""
    try:
        return connect(url, open_timeout=5, close_timeout=2, max_size=None, **kw)
    except InvalidStatus as e:
        pytest.fail(f"WebSocket refused BEFORE accept (HTTP {e.response.status_code}); "
                    "the server must accept first, then close with a code and reason", pytrace=False)


def wait_close(ws, timeout: float, *, until_bytes: bytes | None = None, pause: float = 0.0) -> Outcome:
    """Read until the socket closes, `until_bytes` is seen, or `timeout` passes."""
    out = Outcome()
    t0 = time.monotonic()
    buf = bytearray()
    while True:
        left = timeout - (time.monotonic() - t0)
        if left <= 0:
            break
        try:
            msg = ws.recv(timeout=left)
        except TimeoutError:
            break
        except ConnectionClosed as e:
            out.code = e.rcvd.code if e.rcvd is not None else 1006
            out.reason = e.rcvd.reason if e.rcvd is not None else ""
            out.closed_after = time.monotonic() - t0
            break
        if isinstance(msg, (bytes, bytearray)):
            buf += msg
            if until_bytes is not None and until_bytes in buf:
                break
        else:
            out.texts.append(msg)
        if pause:
            time.sleep(pause)
    out.data = bytes(buf)
    return out


@pytest.fixture
def live_herdr(fake_api):
    """A 'running herdr server': the fake API socket answers every request (pane.list scripted)."""
    fake_api.on("pane.list", result={"panes": []})
    return fake_api
