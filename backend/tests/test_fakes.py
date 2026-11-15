"""One self-test per fake."""
import asyncio
import json
import os
import pty
import select
import subprocess
import sys
import time

import pytest

from app import herdr
from tests.conftest import FAKES


def _run_pty(script, env_extra=None, args=()):
    env = dict(os.environ, **(env_extra or {}))
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(str(FAKES / script), [str(FAKES / script), *args], env)
    return pid, fd


def _read_until(fd, needle: bytes, timeout=3.0) -> bytes:
    buf, end = b"", time.time() + timeout
    while needle not in buf and time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                d = os.read(fd, 4096)
            except OSError:
                break
            if not d:
                break
            buf += d
    return buf


def test_echo_client_echoes_stdin_and_exits_after_n_seconds():
    pid, fd = _run_pty("herdr_echo.py", {"FAKE_HERDR_SECONDS": "1.5", "FAKE_HERDR_EXIT": "7"}, ("--session", "default"))
    assert b"fake-herdr ready" in _read_until(fd, b"ready")
    os.write(fd, b"ping-xyz")
    assert b"ping-xyz" in _read_until(fd, b"ping-xyz")
    t0 = time.time()
    _, status = os.waitpid(pid, 0)
    assert 0.3 < time.time() - t0 < 3.0
    assert os.WEXITSTATUS(status) == 7
    os.close(fd)


def test_echo_client_answers_cli_verbs_with_an_envelope():
    out = subprocess.run([sys.executable, str(FAKES / "herdr_echo.py"), "--session", "default", "pane", "list"],
                         capture_output=True, text=True, timeout=5)
    assert json.loads(out.stdout)["result"] == {}


def test_exit_client_exits_immediately_with_its_status():
    t0 = time.time()
    p = subprocess.run([str(FAKES / "herdr_exit.py")], env=dict(os.environ, FAKE_HERDR_EXIT="9"), timeout=5)
    assert p.returncode == 9 and time.time() - t0 < 2


def test_fake_api_answers_the_shape_socket_request_expects(fake_api):
    fake_api.on("pane.list", result={"panes": [{"pane_id": "w1:p1"}]})
    assert herdr._socket_request("pane.list", {}) == {"panes": [{"pane_id": "w1:p1"}]}
    assert fake_api.requests[-1]["method"] == "pane.list"
    assert fake_api.requests[-1]["id"] == "web:pane.list"


def test_fake_api_scripted_error_handler_and_unscripted(fake_api):
    fake_api.on("pane.focus", error="no such pane")
    with pytest.raises(herdr.HerdrError, match="no such pane"):
        herdr._socket_request("pane.focus", {"pane_id": "x:1"})
    fake_api.on("tab.list", handler=lambda p: {"tabs": [p]})
    assert herdr._socket_request("tab.list", {"a": 1}) == {"tabs": [{"a": 1}]}
    with pytest.raises(herdr.HerdrError, match="unscripted"):
        herdr._socket_request("nope", {})


def test_fake_api_hang_and_close_and_garbage(fake_api, override_settings):
    override_settings(cli_timeout=0.4)
    fake_api.on("a", hang=True)
    with pytest.raises(herdr.HerdrError):
        herdr._socket_request("a", {})
    fake_api.on("b", close=True)
    # a closed connection with no reply parses as an empty reply
    with pytest.raises(herdr.HerdrError, match="bad reply"):
        herdr._socket_request("b", {})
    fake_api.on("c", raw=b"not json\n")
    with pytest.raises(herdr.HerdrError, match="bad reply"):
        herdr._socket_request("c", {})


def test_fake_api_event_stream_like_events_py(fake_api):
    async def go():
        reader, writer = await asyncio.open_unix_connection(herdr.socket_path(), limit=1 << 22)
        writer.write((json.dumps({"id": "web:events", "method": "events.subscribe",
                                  "params": {"subscriptions": [{"type": "pane.created"}]}}) + "\n").encode())
        await writer.drain()
        ack = json.loads(await asyncio.wait_for(reader.readline(), 2))
        assert "event" not in ack and "error" not in ack
        for _ in range(50):
            if fake_api.push_event("pane.agent_status_changed", {"pane_id": "w1:p1", "agent_status": "blocked"}):
                break
            await asyncio.sleep(0.02)
        ev = json.loads(await asyncio.wait_for(reader.readline(), 2))
        assert ev == {"event": "pane.agent_status_changed", "data": {"pane_id": "w1:p1", "agent_status": "blocked"}}
        fake_api.drop_subscribers()
        assert await asyncio.wait_for(reader.readline(), 2) == b""
        writer.close()
    asyncio.run(go())


def test_fake_api_subscribe_rule_can_refuse(fake_api):
    """A scripted events.subscribe error replaces the built-in ack ("subscribe refused" path)."""
    fake_api.on("events.subscribe", error="subscribe refused")

    async def go():
        reader, writer = await asyncio.open_unix_connection(herdr.socket_path())
        writer.write((json.dumps({"id": "web:events", "method": "events.subscribe", "params": {}}) + "\n").encode())
        await writer.drain()
        msg = json.loads(await asyncio.wait_for(reader.readline(), 2))
        assert msg == {"id": "web:events", "error": "subscribe refused"}
        assert await asyncio.wait_for(reader.readline(), 2) == b""
        writer.close()
    asyncio.run(go())
    assert fake_api.push_event("pane.created", {}) == 0
