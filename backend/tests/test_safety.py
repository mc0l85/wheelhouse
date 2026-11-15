import os

import pytest

from tests.conftest import FAKES, real_herdr_home, real_herdr_paths, safety_violations
from app.config import settings


def test_settings_point_at_fakes_not_real_herdr():
    assert os.path.realpath(settings.herdr_bin) == os.path.realpath(FAKES / "herdr_echo.py")
    assert safety_violations(settings.herdr_bin, settings.herdr_home) == []


def test_guard_trips_on_the_real_binary():
    real = sorted(real_herdr_paths())[0]
    assert safety_violations(real, settings.herdr_home), "guard must flag the real binary"


def test_guard_trips_on_bare_herdr_that_resolves_to_real(monkeypatch):
    # `which herdr` is the real one when it is on PATH
    import shutil
    w = shutil.which("herdr")
    if not w:
        pytest.skip("no herdr on PATH")
    assert safety_violations("herdr", settings.herdr_home)


def test_guard_trips_on_the_real_config_dir():
    assert safety_violations(settings.herdr_bin, real_herdr_home())
    assert safety_violations(settings.herdr_bin, "~/.config/herdr/sessions/x")


def test_autouse_gate_fails_when_unsafe(override_settings):
    """The autouse gate (assert_safe) really fails with an unsafe setting, and passes again after."""
    from _pytest.outcomes import Failed
    from tests.conftest import assert_safe
    assert_safe()
    override_settings(herdr_bin=sorted(real_herdr_paths())[0])
    with pytest.raises(Failed, match="UNSAFE TEST CONFIG"):
        assert_safe()


def test_autouse_gate_fails_on_real_home(override_settings):
    from _pytest.outcomes import Failed
    from tests.conftest import assert_safe
    override_settings(herdr_home=real_herdr_home())
    with pytest.raises(Failed, match="real config dir"):
        assert_safe()
