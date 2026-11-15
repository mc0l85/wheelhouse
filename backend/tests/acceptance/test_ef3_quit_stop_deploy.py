"""E-F3: cutover steps 3-6 and 9 on a full offline run.

  3  one `send-keys <pane> ctrl+c ctrl+c` per agent pane, the coordinator's LAST, all before the web
     unit stops; an agent that ignores it gets SIGTERM on its manifest pid.
  4  the old web unit (which holds the old in-cgroup server) is stopped while the OLD tree is in place;
     session.json is restored from the post-quit copy if the stop broke it.
  5  the staged tree is deployed (old tree kept), the new web unit is rendered and started with no herdr
     server running and NO herdr.service unit file yet (its Wants= would start it), the old unit is
     disabled and moved out, the guard is proven (attach -> 4503, no server appears).
  6  herdr.service rendered + started; the repo's herdr-update.sh installed at UPDATE_SCRIPT.
  9  the web unit is restarted once without touching the server; the owner is notified with the result.
Contract: backend/tests/acceptance/cutover_world.py.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from tests.acceptance.cutover_world import OLD_UNIT_TEXT, OLD_UPDATE_TEXT, UPDATE_SH, World, tree_digest

AGENTS = ["wf:p1", "wf:p2", "wf:p3", "wf:p4"]


@pytest.fixture(scope="module")
def done():
    w = World(options={"corrupt_session_on_exit": True}).boot()
    r = w.run("--foreground")
    yield w, r
    w.close()


def quit_calls(w):
    return [e for e in w.calls("herdr") if e["argv"][:2] == ["pane", "send-keys"] and e["argv"][3:] == ["ctrl+c", "ctrl+c"]]


def first(entries):
    assert entries, "expected call not in the ledger"
    return entries[0]


def idx(w, entry):
    return [e["t"] for e in w.ledger()].index(entry["t"])


def test_run_completes(done):
    w, r = done
    assert r.returncode == 0, (r.returncode, r.stdout[-2000:], r.stderr[-3000:])
    assert w.result()["outcome"] == "done"


def test_quit_once_per_agent_coordinator_last_before_stop(done):
    w, _ = done
    q = quit_calls(w)
    panes = [e["argv"][2] for e in q]
    assert sorted(panes) == sorted(AGENTS), f"one quit call per agent pane, got {panes}"
    assert panes[-1] == "wf:p2", f"the coordinator must quit last: {panes}"
    stop = first(w.systemctl("stop", "herdr-web-api.service"))
    assert all(e["t"] < stop["t"] for e in q), "an agent was quit after the web unit stopped"
    assert not [e for e in w.calls("herdr") if e["argv"][:3] == ["pane", "send-keys", "wf:p5"]]


def test_web_unit_stopped_before_deploy_and_new_web_starts_alone(done):
    w, _ = done
    stop = first(w.systemctl("stop", "herdr-web-api.service"))
    assert stop["app_marker"] == "old", "the tree changed before the old web unit stopped"
    start_web = first(w.systemctl("start", "herdr-web.service") + w.systemctl("--now", "herdr-web.service"))
    assert stop["t"] < start_web["t"]
    assert start_web["app_marker"] == w.staged_marker, "the new web unit started on the old tree"
    assert start_web["server_pids"] == [], "a herdr server was running when the new web unit started"
    assert "herdr.service" not in start_web["unit_files"], "herdr.service existed when the web unit started (Wants= would start it)"
    assert "herdr-web-api.service" not in start_web["unit_files"], "the old unit was still installed"
    disable = first(w.systemctl("disable", "herdr-web-api.service"))
    assert disable["t"] < start_web["t"]


def test_session_json_restored_after_a_broken_stop(done):
    w, _ = done
    starts = w.server_state()["session_at_start"]
    assert len(starts) >= 2, starts                      # the boot, then herdr.service
    assert starts[-1]["parses"] is True and starts[-1]["tabs"] == 4, \
        f"herdr.service started on a broken/short session.json: {starts[-1]}"
    run = w.latest()
    for name in ("session.json", "session-snapshot.json", "session.json.after-quit"):
        assert (run / name).is_file(), f"missing manifest copy {name}"
    assert json.loads((run / "session.json").read_text())["workspaces"]


def test_guard_proven_live(done):
    w, _ = done
    attaches = [json.loads(ln) for ln in (w.world / "web_attaches.jsonl").read_text().splitlines()]
    proof = [a for a in attaches if a["unit"] == "herdr-web.service" and not a["herdr_up"]]
    assert proof and proof[0]["new_backend"], f"no guard attach against the new web unit: {attaches}"
    assert w.result()["guard"] == {"code": 4503, "server_appeared": False}


def test_deployed_tree_rendered_units_and_update_script(done):
    w, _ = done
    b, s = w.app / "backend", w.stage / "backend"
    for part in ("app", "static"):
        assert tree_digest(b / part) == tree_digest(s / part), f"{part} is not the staged one"
    assert (b / "requirements.txt").read_text() == (s / "requirements.txt").read_text()
    for kept in (".venv", ".env"):
        assert {k: v for k, v in w.before["app"].items() if k.startswith(kept)} == \
               {k: v for k, v in tree_digest(b).items() if k.startswith(kept)}, f"{kept} changed"
    old = w.latest() / "old-tree"
    assert (old / "app" / "MARKER").read_text() == "old"
    assert (old / "static" / "index.html").read_text() == "<html>old</html>"
    assert (old / "requirements.txt").read_text() == "fastapi==0.1\n"

    web = (w.units / "herdr-web.service").read_text()
    herdr = (w.units / "herdr.service").read_text()
    for text in (web, herdr):
        assert "@" not in text.replace("@.service", ""), "an unrendered @PLACEHOLDER@ is left"
    assert f"WorkingDirectory={w.app}/backend" in web and f"EnvironmentFile={w.conf}/env" in web
    assert f"ExecStart={w.values['HERDR_BIN']} server" in herdr and f"User={w.values['SERVICE_USER']}" in herdr
    assert not (w.units / "herdr-web-api.service").exists()
    assert (w.latest() / "old-units" / "herdr-web-api.service").read_text() == OLD_UNIT_TEXT

    start_herdr = first(w.systemctl("start", "herdr.service") + w.systemctl("--now", "herdr.service"))
    assert "herdr.service" in start_herdr["unit_files"]
    assert w.update_script.read_bytes() == UPDATE_SH.read_bytes()
    assert os.stat(w.update_script).st_mode & stat.S_IXUSR
    assert (w.latest() / "old-update-script").read_text() == OLD_UPDATE_TEXT


def test_one_server_owned_by_herdr_service_and_web_restart_leaves_it(done):
    w, _ = done
    servers = w.server_pids()
    unit_pids = [int(x) for x in (w.world / "units" / "herdr.service").read_text().split()]
    assert len(servers) == 1 and servers[0] in unit_pids, (servers, unit_pids)
    start_herdr = first(w.systemctl("start", "herdr.service") + w.systemctl("--now", "herdr.service"))
    restart = first(w.systemctl("restart", "herdr-web.service"))
    assert restart["t"] > start_herdr["t"]
    wr = w.result()["web_restart"]
    assert wr["server_pid_before"] == wr["server_pid_after"] == servers[0]


def test_owner_notified_once_with_the_result(done):
    w, _ = done
    n = w.calls("hw-notify")
    assert len(n) == 1, n
    assert str(w.latest() / "result.json") in " ".join(n[0]["argv"]) or \
        str(w.state / "latest" / "result.json") in " ".join(n[0]["argv"])
    assert n[0]["t"] >= max(e["t"] for e in w.calls("systemctl"))


def test_agent_ignoring_quit_gets_sigterm():
    w = World()
    for p in w.panes:
        if p["pane_id"] == "wf:p1":
            p["quit"] = "ignore"
    w.boot()
    try:
        sleeper = next(sp for sp in w.sleepers if sp.pid == next(p["pid"] for p in w.panes if p["pane_id"] == "wf:p1"))
        r = w.run("--foreground")
        assert r.returncode in (0, 5), (r.returncode, r.stderr[-3000:])
        assert sleeper.wait(5) == -15, "the manifest pid did not get SIGTERM"
        stop = first(w.systemctl("stop", "herdr-web-api.service"))
        assert stop["t"] > max(e["t"] for e in quit_calls(w))
    finally:
        w.close()
