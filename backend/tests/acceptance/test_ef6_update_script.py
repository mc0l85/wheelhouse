"""E-F6: deploy/herdr-update.sh (the nightly timer's script, installed by the cutover) updates with
plain `herdr update`, never `--handoff` (Decision 10); hooks first; a busy or missing idle-wait skips
only the binary update. Asserted on the fake herdr's recorded ledger.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.acceptance.cutover_world import UPDATE_SH
from tests.conftest import FAKES


def code_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def test_no_handoff_anywhere_in_the_code():
    text = UPDATE_SH.read_text()
    assert UPDATE_SH.stat().st_mode & 0o111, "deploy/herdr-update.sh must be executable"
    assert not [ln for ln in code_lines(text) if "--handoff" in ln]
    assert any(re.search(r"(\bherdr\b|\$\{?HERDR\w*\}?)\"?\s+update\b", ln) for ln in code_lines(text)), \
        "no `herdr update` call"


@pytest.fixture
def env(tmp_path):
    (tmp_path / "bin").mkdir()
    herdr = tmp_path / "bin" / "herdr"
    herdr.symlink_to(FAKES / "herdr_cli.py")
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("")
    e = dict(os.environ, HOME=str(tmp_path), HERDR_BIN=str(herdr), FAKE_LEDGER=str(ledger),
             PATH=f"{tmp_path / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}")
    e.pop("HERDR_SKIP_WAIT", None)
    return tmp_path, e, ledger


def wait_script(tmp_path: Path, rc: int) -> Path:
    p = tmp_path / "wait-idle.py"
    p.write_text(f"#!{sys.executable}\nimport json, sys\nopen({str(tmp_path / 'wait.argv')!r}, 'w').write(json.dumps(sys.argv[1:]))\nsys.exit({rc})\n")
    p.chmod(0o755)
    return p


def herdr_calls(ledger: Path) -> list[list[str]]:
    return [json.loads(ln)["argv"] for ln in ledger.read_text().splitlines() if ln.strip()]


def run(e):
    return subprocess.run([str(UPDATE_SH)], env=e, capture_output=True, text=True, timeout=60)


def test_idle_then_plain_update(env):
    tmp, e, ledger = env
    e["HERDR_WAIT_IDLE"] = str(wait_script(tmp, 0))
    e["HERDR_QUIET_SECS"] = "7"
    r = run(e)
    assert r.returncode == 0, r.stderr
    calls = herdr_calls(ledger)
    updates = [c for c in calls if c[:1] == ["update"]]
    assert updates == [["update"]], f"want exactly one plain `herdr update`, got {updates}"
    installs = {c[2] for c in calls if c[:2] == ["integration", "install"]}
    assert installs == {"antigravity-cli", "opencode"}, installs   # fake status: outdated / not installed
    assert json.loads((tmp / "wait.argv").read_text()) == ["--quiet-secs", "7"]
    first_update = calls.index(["update"])
    assert any(c[:2] == ["integration", "install"] for c in calls[:first_update]), "hooks are installed first"


def test_busy_panes_skip_only_the_update(env):
    tmp, e, ledger = env
    e["HERDR_WAIT_IDLE"] = str(wait_script(tmp, 1))
    r = run(e)
    assert r.returncode == 0, r.stderr
    calls = herdr_calls(ledger)
    assert not [c for c in calls if c[:1] == ["update"]]
    assert [c for c in calls if c[:2] == ["integration", "install"]], "hooks are still (re)installed"


def test_missing_wait_script_fails_closed(env):
    tmp, e, ledger = env
    e["HERDR_WAIT_IDLE"] = str(tmp / "no-such-wait.py")
    r = run(e)
    assert r.returncode == 0, r.stderr
    assert not [c for c in herdr_calls(ledger) if c[:1] == ["update"]]
