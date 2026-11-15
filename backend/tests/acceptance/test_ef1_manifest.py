"""E-F1: cutover step 1 (manifest), --dry-run, the abort on a session-less agent pane, the detach.

Contract: backend/tests/acceptance/cutover_world.py. Offline: fake herdr API server, fake systemctl /
systemd-run on PATH; every assertion is on a recorded ledger, the fake server's input log or files.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.acceptance.cutover_world import CUTOVER, World

READ_VERBS = {("pane", "list"), ("agent", "list"), ("pane", "process-info"), ("pane", "get"),
              ("status", "server"), ("--version",)}


def proc_cmdline(pid: int) -> list[str]:
    return [a.decode() for a in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if a]


@pytest.fixture
def world():
    w = World()
    yield w
    w.close()


def rows(m: dict) -> dict[str, dict]:
    return {r["pane_id"]: r for r in m["panes"]}


def test_dry_run_manifest_lists_every_pane_with_session_and_action(world):
    w = world.boot()
    pids = {p["pane_id"]: p.get("pid") for p in w.panes}
    r = w.run("--dry-run")
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout)
    m = out["manifest"]
    rs = rows(m)
    assert set(rs) == {"wf:p1", "wf:p2", "wf:p3", "wf:p4", "wf:p5"}
    assert m["coordinator"] == "wf:p2"
    assert [pid for pid, row in rs.items() if row["coordinator"]] == ["wf:p2"]
    assert m["tab_count"] == 4
    assert isinstance(m["run_id"], str) and m["run_id"] and "T" in m["started_at"]

    p1 = rs["wf:p1"]
    assert p1["agent"] == "claude" and p1["session"] == {"kind": "id", "value": w.U1}
    assert p1["action"] == "resume"
    assert p1["relaunch"] == ["/x/bin/claude", "--dangerously-skip-permissions", "--resume", w.U1]
    assert p1["argv"] == ["/x/bin/claude", "--dangerously-skip-permissions", "--resume", "stale-id"]
    assert p1["pid"] == pids["wf:p1"]
    assert p1["proc_cmdline"] == proc_cmdline(pids["wf:p1"])

    assert rs["wf:p2"]["relaunch"] == ["/x/bin/claude", "--resume", w.U2]
    p3 = rs["wf:p3"]
    assert p3["agent"] == "pi" and p3["session"] == {"kind": "path", "value": w.PI}
    assert p3["action"] == "resume" and p3["relaunch"] == ["/x/pi/bin/pi", "--session", w.PI]
    p4 = rs["wf:p4"]                                   # a session id but no transcript: never prompted
    assert p4["action"] == "fresh" and p4["relaunch"] == ["/x/bin/claude", "--model", "opus"]
    p5 = rs["wf:p5"]
    assert p5["agent"] is None and p5["action"] == "none" and p5["relaunch"] is None and p5["session"] is None

    plan = out["plan"]
    assert isinstance(plan, list) and all(isinstance(x, str) for x in plan)
    text = "\n".join(plan)
    for needle in ("herdr-web-api.service", "herdr-web.service", "herdr.service",
                   "wf:p1", "wf:p2", "wf:p3", "wf:p4", "ctrl+c ctrl+c"):
        assert needle in text, f"the plan does not mention {needle!r}"


def test_dry_run_changes_nothing(world):
    w = world.boot()
    r = w.run("--dry-run")
    assert r.returncode == 0, r.stderr[-2000:]
    assert w.snapshot() == w.before, "a dry run changed files"
    assert not w.calls("systemctl") and not w.calls("systemd-run") and not w.calls("hw-notify")
    for argv in w.herdr_calls():
        assert tuple(argv[:2]) in READ_VERBS or tuple(argv[:1]) in READ_VERBS, f"dry run ran a mutating herdr verb: {argv}"
    for p in w.panes:
        assert w.inputs(p["pane_id"]) == [], f"dry run sent input to {p['pane_id']}"
    assert w.server_pids() == [w.old_server_pid]
    assert os.listdir(w.state) == []


@pytest.mark.parametrize("dry", [True, False], ids=["dry-run", "real"])
def test_agent_pane_without_session_ref_aborts_with_nothing_changed(world, dry):
    w = world
    w.panes.append(w.agent("wf:p6", "wf:t5", "claude", None, status="blocked", argv=["/x/bin/claude"]))
    w.boot()
    r = w.run("--dry-run") if dry else w.run("--foreground")
    assert r.returncode == 3, (r.returncode, r.stderr[-2000:])
    assert "wf:p6" in r.stdout + r.stderr
    assert not [a for a in w.herdr_calls() if a[:2] in (["pane", "send-keys"], ["pane", "run"], ["pane", "send-text"])]
    assert not w.calls("systemctl") and not w.calls("systemd-run")
    snap = w.snapshot()
    for k in ("app", "units", "update", "session"):
        assert snap[k] == w.before[k], f"abort changed {k}"
    assert w.server_pids() == [w.old_server_pid]
    if dry:
        assert os.listdir(w.state) == []
    else:
        res = w.result()
        assert res["outcome"] == "aborted" and "wf:p6" in res["abort_reason"]


def test_launch_without_foreground_only_detaches(world):
    w = world.boot()
    r = w.run()
    assert r.returncode == 0, r.stderr[-2000:]
    runs = w.calls("systemd-run")
    assert len(runs) == 1, runs
    argv = runs[0]["argv"]
    joined = " ".join(argv)
    assert "--unit=herdr-cutover" in argv or ("--unit" in argv and argv[argv.index("--unit") + 1] == "herdr-cutover")
    assert str(CUTOVER) in joined and str(w.host_env) in joined and "--foreground" in argv
    assert not w.calls("systemctl")
    assert not [a for a in w.herdr_calls() if a[:2] == ["pane", "send-keys"]]
    assert w.snapshot() == w.before


def test_bad_host_env_is_refused_before_anything(world):
    w = world.boot()
    w.host_env.write_text("".join(ln + "\n" for ln in w.host_env.read_text().splitlines()
                                  if not ln.startswith("STAGE_DIR=")))
    r = w.run("--dry-run")
    assert r.returncode == 2
    assert "STAGE_DIR" in r.stderr
    assert not w.herdr_calls() and not w.calls("systemctl")
