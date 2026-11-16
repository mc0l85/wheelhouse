"""S1: --rollback from inside herdr's / the web unit's cgroup re-execs itself detached. Offline."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[2] / "deploy" / "cutover_lib.py"
spec = importlib.util.spec_from_file_location("cutover_lib", LIB)
lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lib)


def make():
    c = object.__new__(lib.Cutover)
    c.v = {"HERDR_UNIT": "herdr.service", "WEB_UNIT": "herdr-web.service", "OLD_WEB_UNIT": "old-web.service"}
    return c


@pytest.mark.parametrize("path,expect", [
    ("/system.slice/herdr.service", True),
    ("/system.slice/herdr-web.service", True),
    ("/system.slice/herdr-rollback.service", False),
    ("/user.slice/user-1000.slice/session-3.scope", False),
])
def test_rollback_needs_detach(path, expect):
    assert make().rollback_needs_detach(lambda: f"0::{path}\n") is expect


def test_unreadable_cgroup_is_a_usage_error():
    def boom():
        raise OSError("nope")
    with pytest.raises(lib.Usage):
        make().cgroup_path(boom)


def test_detach_rollback_command(monkeypatch, capsys):
    c = make()
    c.state = Path("/state")
    c.privileged = lambda *a: list(a)
    seen = {}

    class P:
        returncode, stderr = 0, ""

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return P()
    monkeypatch.setattr(lib.subprocess, "run", run)
    assert c.detach_rollback("/x/cutover.sh", "/h.env") == 0
    assert seen["cmd"] == ["systemd-run", "--unit=herdr-rollback", "--collect", "--description=herdr rollback",
                           "/x/cutover.sh", "--host-env", "/h.env", "--rollback", "--require-unit"]
