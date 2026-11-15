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
import shutil
import termios
import time
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import herdr
from ..config import settings
from ..origin import origin_ok

router = APIRouter(prefix="/api/ws", tags=["terminal"])
OUT_Q_MAX_BYTES = 8 * 1024 * 1024   # queued pty output per client before close 1013 "output backlog"
_live = 0       # open terminal sockets (each one is a herdr client process)


def reset_live_count() -> None:
    """Tests only: forget every live terminal (acceptance tests call this before each server)."""
    global _live
    _live = 0

_STRIP_ENV = ("HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH")


def _spawn(session: str, rows: int, cols: int) -> tuple[int, int]:
    # Build everything in the parent: the child of a threaded process must do nothing but exec.
    env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}
    env["TERM"] = "xterm-256color"
    herdr_bin = settings.herdr_bin
    argv = [herdr_bin, "--session", session]
    pid, fd = pty.fork()
    if pid == 0:  # child → become a herdr client
        try:
            os.execvpe(herdr_bin, argv, env)
        finally:
            os._exit(127)
    try:
        os.set_inheritable(fd, False)   # no other herdr client may inherit this master fd
        _set_winsize(fd, rows, cols)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        raise
    return pid, fd


_RING = 256 * 1024     # bytes of output kept per client for a glitch report
_GLITCH_KEEP = 20
_IN_CAP = 8 * 1024 * 1024  # input queued for the pty, max; a bigger frame is refused whole


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
        try:
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:      # server shutdown: still leave no orphan
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass
            raise
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except (ProcessLookupError, ChildProcessError):
        pass


@router.websocket("/terminal")
async def terminal(ws: WebSocket) -> None:
    # CORS does not apply to WebSockets: without this, any web page the user
    # visits could open a shell here through their browser (close 1008 = policy).
    await ws.accept()      # accept first: a pre-accept close reaches the browser as HTTP 403, no reason
    if not origin_ok(ws.headers):
        await ws.close(code=1008, reason="origin not allowed")
        return
    global _live
    if _live >= settings.max_terminals:
        await ws.close(code=1013, reason="terminal limit reached")   # try again later
        return
    _live += 1          # claimed before any await so concurrent connects cannot all pass the limit
    pid = fd = -1
    loop = asyncio.get_running_loop()
    try:
        if not shutil.which(settings.herdr_bin):
            await ws.close(code=4500, reason="herdr binary missing")
            return
        state = await asyncio.to_thread(herdr.probe, settings.probe_timeout)
        if state != "ok":
            await ws.close(code=4503, reason="herdr server not responding" if state == "silent"
                           else "herdr server not running")
            return
        # The session is the service's, not the browser's: a client must not be
        # able to attach to some other herdr session on the box.
        session = settings.herdr_session
        rows = _clamp(ws.query_params.get("rows"), 40)
        cols = _clamp(ws.query_params.get("cols"), 120)
        try:
            pid, fd = _spawn(session, rows, cols)
        except OSError:
            await ws.close(code=1011, reason="could not start herdr client")
            return
        os.set_blocking(fd, False)
        await _bridge(ws, loop, pid, fd)
    finally:
        _live -= 1
        if fd >= 0:
            try:
                loop.remove_reader(fd)
                loop.remove_writer(fd)
            except Exception:
                pass
            try:
                os.close(fd)
            except OSError:
                pass
        if pid > 0:
            await _reap(pid)


async def _bridge(ws: WebSocket, loop: asyncio.AbstractEventLoop, pid: int, fd: int) -> None:
    out_q: asyncio.Queue[tuple[bytes, str]] = asyncio.Queue()
    queued = 0          # bytes in out_q not yet handed to the socket

    # Non-blocking reads on the event loop: no executor thread left parked in
    # read() after close, so a fast reconnect can't race a reused fd number.
    def _on_readable() -> None:
        nonlocal queued
        # Take everything that is ready (bounded to 256 KiB) in one callback, in 64 KiB reads,
        # so a bulk flood is not paced by one event-loop turn per read.
        chunks: list[bytes] = []
        got = 0
        eof = False
        while got < 262144:
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                break
            except OSError:
                eof = True
                break
            if not data:
                eof = True
                break
            chunks.append(data)
            got += len(data)
        if chunks:
            # Backlog beats EOF: if both happen in this callback the close is 1013, not 1012
            # (the reader is removed here, so the EOF is never queued).
            if queued + got > OUT_Q_MAX_BYTES:
                # The browser is not keeping up: stop reading, let what is queued drain in
                # order, then close; the client reconnects and herdr repaints.
                out_q.put_nowait((b"", "backlog"))
                loop.remove_reader(fd)
                return
            queued += got
            out_q.put_nowait((b"".join(chunks), ""))
        if eof:
            out_q.put_nowait((b"", "exit"))
            loop.remove_reader(fd)

    loop.add_reader(fd, _on_readable)

    # The last bytes herdr sent this client, for a glitch report (see below).
    ring = bytearray()

    async def pump_out() -> None:
        nonlocal queued
        while True:
            data, why = await out_q.get()
            if data:
                queued -= len(data)
                ring.extend(data)
                if len(ring) > _RING:
                    del ring[:len(ring) - _RING]
                try:
                    await ws.send_bytes(data)
                except Exception:
                    break
            if why:
                try:
                    if why == "backlog":
                        await ws.close(code=1013, reason="output backlog")
                    else:
                        await ws.close(code=1012, reason="herdr client exited")
                except Exception:
                    pass
                break

    # Input is queued and drained as the pty accepts it. A raw pty takes only
    # ~11.5 KB per non-blocking write; the old code ignored os.write's count, so
    # a paste past that lost its tail, INCLUDING the bracketed-paste end marker
    # (ESC[201~), and herdr sat in paste mode swallowing every key until reload.
    pending = bytearray()
    writer_on = False

    def _flush() -> None:
        nonlocal writer_on
        while pending:
            try:
                n = os.write(fd, pending)
            except BlockingIOError:
                break
            except OSError:          # client gone: the read side ends the socket
                pending.clear()
                break
            del pending[:n]
        if pending and not writer_on:
            loop.add_writer(fd, _flush)
            writer_on = True
        elif not pending and writer_on:
            loop.remove_writer(fd)
            writer_on = False

    def _write(data: bytes) -> None:
        # A paste arrives as ONE frame, so refusing a whole frame past the cap
        # never splits a bracketed paste.
        if len(pending) + len(data) > _IN_CAP:
            return
        pending.extend(data)
        _flush()

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
            elif obj is not None and "type" in obj:
                continue                                 # unknown control type: dropped
            elif settings.enable_writes:                 # plain keystroke text
                _write(text.encode())
    except WebSocketDisconnect:
        pass
    finally:
        out.cancel()
        loop.remove_reader(fd)
        loop.remove_writer(fd)
