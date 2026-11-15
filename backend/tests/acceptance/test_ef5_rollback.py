"""E-F5: rollback. A failure in step 5/6 rolls back by itself (exit 4); `--rollback` undoes a finished
run. Both end byte-identical to before: the old backend tree, the old unit file (and no new ones),
the old nightly script; the old web unit enabled and running again, herdr.service stopped first.
Contract: backend/tests/acceptance/cutover_world.py.
"""
from __future__ import annotations

from tests.acceptance.cutover_world import World


def assert_restored(w):
    snap = w.snapshot()
    assert snap["app"] == w.before["app"], "the old backend tree was not restored byte for byte"
    assert snap["units"] == w.before["units"], f"unit dir differs: {sorted(snap['units'])}"
    assert snap["update"] == w.before["update"], "the old nightly script was not restored"
    old = w.systemctl("start", "herdr-web-api.service") + w.systemctl("--now", "herdr-web-api.service")
    assert old, "the old web unit was not started"
    last = old[-1]
    assert last["app_marker"] == "old", "the old unit started before the old tree was back"
    assert "herdr-web-api.service" in last["unit_files"]
    assert "herdr-web.service" not in last["unit_files"] and "herdr.service" not in last["unit_files"]
    assert w.systemctl("enable", "herdr-web-api.service"), "the old web unit was not re-enabled"
    pids = [int(x) for x in (w.world / "units" / "herdr-web-api.service").read_text().split()]
    from tests.fakes.systemd_fake import alive
    assert any(alive(p) for p in pids), "the old web unit is not running"
    return last


def test_failure_in_step_6_rolls_back_by_itself():
    w = World(options={"herdr_unit_fails": True}).boot()
    try:
        r = w.run("--foreground")
        assert r.returncode == 4, (r.returncode, r.stderr[-3000:])
        assert w.result()["outcome"] == "rolled_back"
        last = assert_restored(w)
        stop_web = w.systemctl("stop", "herdr-web.service")
        assert stop_web and stop_web[-1]["t"] < last["t"]
    finally:
        w.close()


def test_rollback_after_a_finished_run():
    w = World().boot()
    try:
        r = w.run("--foreground")
        assert r.returncode == 0, (r.returncode, r.stderr[-3000:])
        mark = len(w.ledger())
        r = w.run("--rollback")
        assert r.returncode == 0, (r.returncode, r.stderr[-3000:])
        last = assert_restored(w)
        after = w.ledger()[mark:]
        stops = [e for e in after if e["tool"] == "systemctl" and "stop" in e["argv"] and "herdr.service" in e["argv"]]
        assert stops and stops[0]["t"] < last["t"], "herdr.service must stop before the old web unit starts"
        assert not [p for p in w.server_pids()
                    if str(p) in (w.world / "units" / "herdr.service").read_text().split()]
    finally:
        w.close()


def test_rollback_without_a_run_refuses():
    w = World().boot()
    try:
        r = w.run("--rollback")
        assert r.returncode == 2
        assert not w.calls("systemctl")
        assert w.snapshot() == w.before
    finally:
        w.close()
