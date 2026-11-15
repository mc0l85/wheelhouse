"""Test harness: isolation from the REAL herdr comes first.

The env is forced BEFORE `app.config` is imported (Settings reads it at import):
HERDR_BIN -> a fake client script, HERDR_HOME -> a scratch dir, state dir -> scratch.
An autouse fixture (and a session-start check) FAILS the run if settings.herdr_bin
resolves to the real herdr binary or herdr_home is the real ~/.config/herdr.

Run:  cd backend && python3 -m pytest -q tests
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

FAKES = Path(__file__).resolve().parent / "fakes"
# mkdtemp (honours $TMPDIR): the env must be forced at import time, before any fixture exists.
_ROOT = tempfile.mkdtemp(prefix="hwt-")
_HOME = os.path.join(_ROOT, "herdr-home")
os.makedirs(_HOME, exist_ok=True)

# Nothing inherited from the shell: every HERDRWEB_* setting (e.g. a deployment's
# HERDRWEB_ALLOWED_HOSTS, read at import) is removed, then the test values are forced.
for _k in [k for k in os.environ if k.startswith("HERDRWEB_")]:
    del os.environ[_k]
# Forced, never setdefault: an inherited HERDR_BIN pointing at the real binary must not survive.
os.environ["HERDR_BIN"] = str(FAKES / "herdr_echo.py")
os.environ["HERDR_HOME"] = _HOME
os.environ["HERDR_SESSION"] = "default"
os.environ["HERDRWEB_STATE_DIR"] = os.path.join(_ROOT, "state")
os.environ["HERDRWEB_ENABLE_WRITES"] = "0"

from app.config import settings  # noqa: E402  (after the env is forced)


def real_herdr_paths() -> set[str]:
    paths = {os.path.realpath(os.path.expanduser("~/.local/bin/herdr"))}
    w = shutil.which("herdr", path=os.environ.get("HERDR_REAL_PATH") or _original_path())
    if w:
        paths.add(os.path.realpath(w))
    return paths


def _original_path() -> str:
    return os.environ.get("PATH", "")


def real_herdr_home() -> str:
    return os.path.realpath(os.path.expanduser("~/.config/herdr"))


def safety_violations(herdr_bin: str, herdr_home: str) -> list[str]:
    """Why this configuration would touch the real herdr (empty = safe)."""
    bad = []
    bin_real = os.path.realpath(shutil.which(herdr_bin) or herdr_bin)
    if bin_real in real_herdr_paths():
        bad.append(f"herdr_bin resolves to the real herdr binary: {bin_real}")
    home_real = os.path.realpath(os.path.expanduser(herdr_home))
    real_home = real_herdr_home()
    if home_real == real_home or home_real.startswith(real_home + os.sep):
        bad.append(f"herdr home is the real config dir: {home_real}")
    return bad


def pytest_sessionstart(session):
    bad = safety_violations(settings.herdr_bin, settings.herdr_home)
    if bad:
        pytest.exit("UNSAFE TEST CONFIG: " + "; ".join(bad), returncode=3)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_ROOT, ignore_errors=True)


def assert_safe() -> None:
    bad = safety_violations(settings.herdr_bin, settings.herdr_home)
    if bad:
        pytest.fail("UNSAFE TEST CONFIG: " + "; ".join(bad), pytrace=False)


@pytest.fixture(autouse=True)
def _never_the_real_herdr():
    assert_safe()
    yield


@pytest.fixture
def herdr_home() -> str:
    return settings.herdr_home


def _set(name: str, value):
    """Settings is a frozen dataclass; tests override fields and restore after."""
    old = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old


@pytest.fixture
def override_settings():
    saved: dict[str, object] = {}

    def apply(**kw):
        for k, v in kw.items():
            saved.setdefault(k, _set(k, v))
    yield apply
    for k, v in saved.items():
        object.__setattr__(settings, k, v)


@pytest.fixture
def fake_api(override_settings):
    """A running fake herdr API socket at the path herdr.socket_path() resolves to."""
    from app import herdr
    from tests.fakes.fake_herdr_api import FakeHerdrApi
    override_settings(herdr_session="default", cli_timeout=3.0)
    api = FakeHerdrApi(herdr.socket_path()).start()
    yield api
    api.stop()
