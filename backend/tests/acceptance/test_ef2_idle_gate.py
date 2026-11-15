"""E-F2: cutover step 2, the idle gate. Blocked -> abort at once; working -> wait up to IDLE_WAIT_S,
then abort; working that turns idle inside the wait -> go on; --force -> go on regardless.
Contract: backend/tests/acceptance/cutover_world.py.
"""
from __future__ import annotations

import time

import pytest

from tests.acceptance.cutover_world import World


@pytest.fixture
def world():
    w = World()
    yield w
    w.close()


def quits(w) -> list[list[str]]:
    return [a for a in w.herdr_calls() if a[:2] == ["pane", "send-keys"] and a[3:] == ["ctrl+c", "ctrl+c"]]


def set_status(w, pane_id, status, **kw):
    for p in w.panes:
        if p["pane_id"] == pane_id:
            p["agent_status"] = status
            p.update(kw)


def test_blocked_agent_aborts_at_once(world):
    w = world
    set_status(w, "wf:p3", "blocked")
    w.boot()
    t0 = time.time()
    r = w.run("--foreground")
    took = time.time() - t0
    assert r.returncode == 3, (r.returncode, r.stderr[-2000:])
    assert took < float(w.timings["IDLE_WAIT_S"]), f"a blocked pane must abort without waiting ({took:.1f} s)"
    assert quits(w) == [] and not w.calls("systemctl")
    res = w.result()
    assert res["outcome"] == "aborted" and "wf:p3" in res["abort_reason"] and "blocked" in res["abort_reason"]
    assert w.snapshot()["app"] == w.before["app"]


def test_still_working_after_the_wait_aborts(world):
    w = world
    set_status(w, "wf:p1", "working")
    w.boot()
    t0 = time.time()
    r = w.run("--foreground")
    took = time.time() - t0
    assert r.returncode == 3, (r.returncode, r.stderr[-2000:])
    assert took >= float(w.timings["IDLE_WAIT_S"]) - 0.2, f"gave up after {took:.1f} s, before IDLE_WAIT_S"
    assert quits(w) == [] and not w.calls("systemctl")
    res = w.result()
    assert res["outcome"] == "aborted" and "wf:p1" in res["abort_reason"]


def test_working_that_turns_idle_inside_the_wait_proceeds(world):
    w = world
    set_status(w, "wf:p1", "working", idle_after_s=1.5)
    w.boot()
    r = w.run("--foreground")
    assert r.returncode == 0, (r.returncode, r.stderr[-3000:])
    idle_at = [t for t, s in w.status_log("wf:p1") if s == "idle"]
    assert idle_at, "the fake never turned p1 idle"
    first_quit = min(e["t"] for e in w.calls("herdr") if e["argv"][:2] == ["pane", "send-keys"])
    assert first_quit >= idle_at[0], "quit started before the working pane went idle"


@pytest.mark.parametrize("status", ["blocked", "working"])
def test_force_passes_the_gate(world, status):
    w = world
    set_status(w, "wf:p3", status)
    w.boot()
    r = w.run("--foreground", "--force")
    assert r.returncode in (0, 5), (r.returncode, r.stderr[-3000:])
    assert ["pane", "send-keys", "wf:p3", "ctrl+c", "ctrl+c"] in quits(w)
    assert w.systemctl("stop", "herdr-web-api.service")
