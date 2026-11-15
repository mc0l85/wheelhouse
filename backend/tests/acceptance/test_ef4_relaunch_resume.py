"""E-F4: cutover steps 7-8. Relaunch per manifest row (Decision 11): `claude --resume <uuid>`,
`pi --session <path>`, plain argv when the agent never got a prompt; skip a pane that came back by
native resume; wait for the manifest session id, never retry blindly. Then `resume` + Enter only once
the agent is idle, skipped when it is blocked; coordinator last in both steps.
Contract: backend/tests/acceptance/cutover_world.py.
"""
from __future__ import annotations

import shlex

import pytest

from tests.acceptance.cutover_world import World


def runs(w, pane_id):
    return [i for i in w.inputs(pane_id) if i["kind"] == "run" and shlex.split(i["data"])[:1]
            and shlex.split(i["data"])[0].rsplit("/", 1)[-1] in ("claude", "pi")]


def resume_time(w, pane_id):
    """Time of `resume` + Enter: a run "resume", or text "resume" followed by an enter key."""
    ins = w.inputs(pane_id)
    for k, i in enumerate(ins):
        if i["kind"] == "run" and i["data"].strip() == "resume":
            return i["t"]
        if i["kind"] == "text" and i["data"] == "resume":
            for j in ins[k + 1:]:
                if j["kind"] == "keys" and any(x.lower() in ("enter", "return") for x in j["data"]):
                    return i["t"]
    return None


def agent_rows(w):
    return {a["pane_id"]: a for a in w.result()["agents"]}


@pytest.fixture(scope="module")
def done():
    w = World()
    for p in w.panes:
        if p["pane_id"] == "wf:p3":                     # pi comes back working, idle after 2 s
            p.update(relaunch_status="working", working_s=2.0)
    w.boot()
    r = w.run("--foreground")
    yield w, r
    w.close()


def test_relaunch_command_per_manifest_row(done):
    w, r = done
    assert r.returncode == 0, (r.returncode, r.stderr[-3000:])
    want = {
        "wf:p1": ["/x/bin/claude", "--dangerously-skip-permissions", "--resume", w.U1],
        "wf:p2": ["/x/bin/claude", "--resume", w.U2],
        "wf:p3": ["/x/pi/bin/pi", "--session", w.PI],
        "wf:p4": ["/x/bin/claude", "--model", "opus"],
    }
    for pane, argv in want.items():
        got = [shlex.split(i["data"]) for i in runs(w, pane)]
        assert got == [argv], f"{pane}: relaunched with {got}, want exactly [{argv}]"
    assert runs(w, "wf:p5") == []
    rows = agent_rows(w)
    for pane in ("wf:p1", "wf:p2", "wf:p3"):
        assert rows[pane]["relaunched"] and rows[pane]["session_match"], rows[pane]
    assert rows["wf:p4"]["relaunched"]
    panes = {p["pane_id"]: p for p in w.server_state()["panes"]}
    assert panes["wf:p1"]["agent_session"]["value"] == w.U1
    assert panes["wf:p3"]["agent_session"]["value"] == w.PI


def test_relaunch_after_herdr_service_and_coordinator_last(done):
    w, _ = done
    start_herdr = [e for e in w.calls("systemctl") if "herdr.service" in e["argv"] and ("start" in e["argv"] or "--now" in e["argv"])]
    assert start_herdr
    times = {p: runs(w, p)[0]["t"] for p in ("wf:p1", "wf:p2", "wf:p3", "wf:p4")}
    assert min(times.values()) > start_herdr[0]["t"]
    assert max(times, key=times.get) == "wf:p2", f"the coordinator must be relaunched last: {times}"


def test_resume_only_after_idle_and_coordinator_last(done):
    w, _ = done
    t = {p: resume_time(w, p) for p in ("wf:p1", "wf:p2", "wf:p3")}
    assert all(t.values()), f"resume + Enter missing: {t}"
    for p, when in t.items():
        assert when > runs(w, p)[0]["t"]
    idle = [ts for ts, s in w.status_log("wf:p3") if s == "idle" and ts > runs(w, "wf:p3")[0]["t"]]
    assert idle and t["wf:p3"] >= idle[0], "resume was sent to pi while it was still working"
    assert max(t, key=t.get) == "wf:p2", f"the coordinator must be resumed last: {t}"
    assert resume_time(w, "wf:p4") is None, "a fresh agent (no conversation) got `resume`"
    rows = agent_rows(w)
    assert all(rows[p]["resume_sent"] for p in t)


def test_blocked_after_relaunch_skips_resume():
    w = World()
    for p in w.panes:
        if p["pane_id"] == "wf:p1":
            p["relaunch_status"] = "blocked"
    w.boot()
    try:
        r = w.run("--foreground")
        assert r.returncode == 5, (r.returncode, r.stderr[-3000:])
        assert resume_time(w, "wf:p1") is None
        assert not [i for i in w.inputs("wf:p1") if i["kind"] == "text" and i["data"] == "resume"]
        row = agent_rows(w)["wf:p1"]
        assert row["resume_sent"] is False and row["skipped"] and "blocked" in row["skipped"]
        assert resume_time(w, "wf:p2") is not None
        assert w.result()["outcome"] == "problems"
    finally:
        w.close()


def test_session_mismatch_is_recorded_not_retried():
    w = World()
    for p in w.panes:
        if p["pane_id"] == "wf:p1":
            p["relaunch_session"] = "some-other-session"
    w.boot()
    try:
        r = w.run("--foreground")
        assert r.returncode == 5, (r.returncode, r.stderr[-3000:])
        assert len(runs(w, "wf:p1")) == 1, "a mismatched relaunch was retried"
        assert agent_rows(w)["wf:p1"]["session_match"] is False
        assert w.result()["outcome"] == "problems"
    finally:
        w.close()


def test_native_resume_is_not_relaunched():
    w = World()
    for p in w.panes:
        if p["pane_id"] == "wf:p1":                     # SIGTERM fallback keeps agent_session -> native resume
            p["quit"] = "ignore"
    w.boot()
    try:
        r = w.run("--foreground")
        assert r.returncode == 0, (r.returncode, r.stderr[-3000:])
        assert runs(w, "wf:p1") == [], "a natively resumed agent was relaunched on top of itself"
        row = agent_rows(w)["wf:p1"]
        assert row["native"] is True and row["session_match"] is True and row["relaunched"] is False
        assert resume_time(w, "wf:p1") is not None
    finally:
        w.close()
