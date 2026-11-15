"""B-7: a bracketed IPv6 loopback Host is accepted by the DEFAULT allowlist
(HERDRWEB_ALLOWED_HOSTS unset); other hosts are still refused.

Runs the app in a fresh interpreter with HERDRWEB_ALLOWED_HOSTS removed from the environment,
because the allowlist is read when app.main is imported.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
ACCEPT = ["[::1]:8086", "[::1]", "localhost:8086", "127.0.0.1:8086"]
REFUSE = ["evil.example", "evil.example:8086", "[::2]:8086", "[::1].evil.example", "[::1]evil:8086"]

PROBE = textwrap.dedent("""
    import json, sys
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    print(json.dumps({h: c.get("/api/health", headers={"host": h}).status_code for h in sys.argv[1:]}))
""")


@pytest.fixture(scope="module")
def statuses(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("b7")
    env = {k: v for k, v in os.environ.items() if k != "HERDRWEB_ALLOWED_HOSTS"}
    env.update(PYTHONPATH=str(BACKEND), PYTHONDONTWRITEBYTECODE="1",
               HERDRWEB_STATE_DIR=str(tmp / "state"), HERDR_HOME=str(tmp / "home"))
    os.makedirs(tmp / "home", exist_ok=True)
    p = subprocess.run([sys.executable, "-c", PROBE, *ACCEPT, *REFUSE], cwd=BACKEND, env=env,
                       capture_output=True, text=True, timeout=60)
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
    assert lines, f"probe failed rc={p.returncode}: {p.stderr[-600:]}"
    return json.loads(lines[-1])


@pytest.mark.parametrize("host", ACCEPT)
def test_default_allowlist_accepts(statuses, host):
    assert statuses[host] == 200, f"Host {host!r} -> {statuses[host]}"


@pytest.mark.parametrize("host", REFUSE)
def test_default_allowlist_refuses(statuses, host):
    assert statuses[host] == 400, f"Host {host!r} -> {statuses[host]}"
