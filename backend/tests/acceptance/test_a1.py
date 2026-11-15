"""A-1: missing herdr binary -> close 4500 with a reason, after accept, no fork;
forced exec failure in `_spawn` -> the child exits 127 (waitpid), no Python cleanup in the child.
"""
from __future__ import annotations

import json
import os
import pty
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.acceptance.conftest import open_ws, wait_close

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture
def fork_recorder(monkeypatch):
    """Record (and refuse) every fork path, so a missing binary can never clone the server."""
    calls: list[str] = []

    def refuse(name):
        def f(*a, **k):
            calls.append(name)
            raise OSError("fork refused by the A-1 test")
        return f
    monkeypatch.setattr(pty, "fork", refuse("pty.fork"))
    monkeypatch.setattr(os, "fork", refuse("os.fork"))
    monkeypatch.setattr(os, "forkpty", refuse("os.forkpty"))
    return calls


@pytest.mark.parametrize("missing", ["absolute", "bare-name"])
def test_missing_binary_closes_4500_after_accept_without_forking(
        missing, live_server, live_herdr, override_settings, fork_recorder, tmp_path):
    # herdr server is "running" (fake API answers), only the client binary is missing.
    bin_ = str(tmp_path / "no-such-herdr") if missing == "absolute" else "herdr-missing-a1-test"
    override_settings(herdr_bin=bin_)
    ws = open_ws(live_server.url())
    out = wait_close(ws, 5)
    assert out.code == 4500, f"expected close 4500 (herdr binary missing), got {out.code} {out.reason!r}"
    assert out.reason.strip(), "the 4500 close must carry a human-readable reason"
    assert fork_recorder == [], f"a missing binary must not fork: {fork_recorder}"


HELPER = textwrap.dedent("""
    import atexit, json, os, select, sys, time
    marker = sys.argv[1]
    parent = os.getpid()

    def note(what):
        if os.getpid() != parent:              # only a child that ran Python code writes this
            with open(marker, "a") as f:
                f.write(what + "\\n")

    atexit.register(note, "atexit")
    from app.routers import terminal
    try:
        pid, fd = terminal._spawn("default", 24, 80)
    finally:
        note("unwound")                        # a child whose exec failed and unwound reaches this
    status = None
    deadline = time.time() + 5
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                os.read(fd, 65536)
            except OSError:
                pass
        done, st = os.waitpid(pid, os.WNOHANG)
        if done:
            status = st
            break
    if status is None:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
    print(json.dumps({"status": status}))
""")


def test_exec_failure_in_spawn_child_exits_127_without_python_cleanup(tmp_path):
    """Run `_spawn` in a separate interpreter (a buggy child would otherwise be a clone of pytest)."""
    not_exec = tmp_path / "herdr-not-executable"
    not_exec.write_text("#!/bin/sh\nexit 0\n")
    not_exec.chmod(0o644)                     # exists, but execvp fails with EACCES
    home = tmp_path / "home"
    home.mkdir()
    helper = tmp_path / "helper.py"
    helper.write_text(HELPER)
    marker = tmp_path / "child-ran-python"
    env = dict(os.environ, HERDR_BIN=str(not_exec), HERDR_HOME=str(home),
               HERDRWEB_STATE_DIR=str(tmp_path / "state"), PYTHONPATH=str(BACKEND),
               PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run([sys.executable, str(helper), str(marker)], cwd=BACKEND, env=env,
                       capture_output=True, text=True, timeout=30)
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
    assert lines, f"helper produced no result (rc={p.returncode}): {p.stdout[-800:]} {p.stderr[-800:]}"
    status = json.loads(lines[-1])["status"]
    assert status is not None, "the child never exited (it kept running Python after a failed exec)"
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 127, \
        f"exec failure must end the child with status 127, got waitpid status {status:#x}"
    assert not marker.exists(), f"the child ran Python cleanup after exec failed: {marker.read_text()!r}"
