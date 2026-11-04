"""WebSocket ↔ PTY bridge — a REAL herdr client in the browser.

On connect we spawn an actual `herdr --session <name>` client inside a pty and
bridge it bidirectionally to the browser's xterm.js over a WebSocket:

  pty (herdr TUI, full ANSI/alt-screen) ──▶ ws.send_bytes ──▶ xterm.write
  xterm.onData ──▶ ws ──▶ os.write(pty)   (keystrokes; only when writes enabled)

Because it is a genuine herdr client, the browser shows EXACTLY what the terminal
shows — every workspace/tab/pane as herdr draws them — and (with writes on) is
fully interactive.

Nesting note: herdr refuses to launch inside a herdr-managed pane. We strip the
HERDR_* env so the bridge works even when the backend itself was started from
within herdr (under systemd the env is already clean).

Resize note: herdr is a shared session (like tmux attach) — resizing this client
can resize the session for OTHER attached clients. The frontend fits to its
container; keep the browser window generous to avoid shrinking your terminal.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import fcntl
import json
import os
import pty
import signal
import struct
import secrets
import termios
import time
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import settings
from ..origin import origin_ok

router = APIRouter(prefix="/api/ws", tags=["terminal"])
_live = 0       # open terminal sockets (each one is a herdr client process)

_STRIP_ENV = ("HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH")


def _spawn(session: str, rows: int, cols: int) -> tuple[int, int]:
    pid, fd = pty.fork()
    if pid == 0:  # child → become a herdr client
        for k in _STRIP_ENV:
            os.environ.pop(k, None)
        os.environ["TERM"] = "xterm-256color"
        os.execvp(settings.herdr_bin, [settings.herdr_bin, "--session", session])
        os._exit(127)
    _set_winsize(fd, rows, cols)
    return pid, fd


_RING = 256 * 1024     # bytes of output kept per client for a glitch report
_GLITCH_KEEP = 20


def _save_glitch(server: bytes, client_b64: str) -> None:
    """Write <state dir>/glitch/<ts>-server.bin and -client.bin (best effort)."""
    try:
        client = base64.b64decode(client_b64, validate=True)
    except (binascii.Error, ValueError):
        client = b""
    d = Path(settings.state_dir).expanduser() / "glitch"
    d.mkdir(parents=True, exist_ok=True)
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    (d / f"{stamp}-server.bin").write_bytes(server)
    (d / f"{stamp}-client.bin").write_bytes(client[:_RING])
    for old in sorted(d.glob("*.bin"), reverse=True)[_GLITCH_KEEP * 2:]:
        old.unlink(missing_ok=True)


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _clamp(v: object, default: int) -> int:
    try:
        return max(2, min(500, int(v)))       # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


async def _reap(pid: int) -> None:
    """SIGTERM the herdr client, then waitpid so it never lingers as a zombie;
    SIGKILL after 2 s if it ignores the TERM."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    for _ in range(20):
        try:
            done, _status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if done:
            return
        await asyncio.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except (ProcessLookupError, ChildProcessError):
        pass


@router.websocket("/terminal")
async def terminal(ws: WebSocket) -> None:
    # CORS does not apply to WebSockets: without this, any web page the user
    # visits could open a shell here through their browser (close 1008 = policy).
    if not origin_ok(ws.headers):
        await ws.close(code=1008)
        return
    global _live
    if _live >= settings.max_terminals:
        await ws.close(code=1013)               # try again later
        return
    await ws.accept()
    _live += 1
    # The session is the service's, not the browser's: a client must not be
    # able to attach to some other herdr session on the box.
    session = settings.herdr_session
    rows = _clamp(ws.query_params.get("rows"), 40)
    cols = _clamp(ws.query_params.get("cols"), 120)

    pid, fd = _spawn(session, rows, cols)
    os.set_blocking(fd, False)
    loop = asyncio.get_running_loop()
    out_q: asyncio.Queue[bytes] = asyncio.Queue()

    # Non-blocking reads on the event loop: no executor thread left parked in
    # read() after close, so a fast reconnect can't race a reused fd number.
    def _on_readable() -> None:
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        out_q.put_nowait(data)
        if not data:
            loop.remove_reader(fd)

    loop.add_reader(fd, _on_readable)

    # The last bytes herdr sent this client, for a glitch report (see below).
    ring = bytearray()

    async def pump_out() -> None:
        while True:
            data = await out_q.get()
            if not data:
                break
            ring.extend(data)
            if len(ring) > _RING:
                del ring[:len(ring) - _RING]
            try:
                await ws.send_bytes(data)
            except Exception:
                break

    def _write(data: bytes) -> None:
        try:
            os.write(fd, data)
        except BlockingIOError:   # pty input buffer full: drop rather than stall
            pass

    out = asyncio.create_task(pump_out())
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            # Binary keystrokes → pty (only when interactivity is enabled).
            if msg.get("bytes") is not None:
                if settings.enable_writes:
                    _write(msg["bytes"])
                continue
            text = msg.get("text")
            if not text:
                continue
            # A text frame is a control message ONLY if it parses to a JSON
            # object with a "type". Anything else (e.g. the keystroke "1", which
            # is valid JSON for the int 1, or pasted text) is forwarded verbatim.
            obj = None
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    obj = parsed
            except json.JSONDecodeError:
                pass
            if obj is not None and obj.get("type") == "ping":
                # Client heartbeat: a text frame back proves the socket is alive
                # (terminal output is always binary, so text = control only).
                await ws.send_text('{"type":"pong"}')
            elif obj is not None and obj.get("type") == "resize":
                # A malformed frame is ignored, never fatal to the session.
                r, c = _clamp(obj.get("rows"), 0), _clamp(obj.get("cols"), 0)
                if r and c:
                    _set_winsize(fd, r, c)
            elif obj is not None and obj.get("type") == "glitch":
                # Controls › Redraw: the screen looked corrupted. Keep what herdr
                # sent and what the browser received, so the two can be diffed.
                _save_glitch(bytes(ring), str(obj.get("client", ""))[: _RING * 2])
            elif obj is not None and obj.get("type") == "input":
                if settings.enable_writes:
                    _write(str(obj.get("data", "")).encode())
            elif settings.enable_writes:                 # plain keystroke text
                _write(text.encode())
    except WebSocketDisconnect:
        pass
    finally:
        _live -= 1
        out.cancel()
        loop.remove_reader(fd)
        try:
            os.close(fd)
        except OSError:
            pass
        await _reap(pid)
