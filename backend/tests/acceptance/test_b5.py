"""B-5: the unit files parse as INI (configparser, strict=False) and carry the PRD 4.3 keys.

herdr.service: system unit; User=/Group= the service user (not root); clean Environment= with
HOME, PATH, SHELL, LANG (Decision 12: LANG=C, LC_CTYPE=C.UTF-8) and nothing HERDRWEB_*; no
EnvironmentFile; ExecStart=<herdr> server; Restart=always; KillMode=mixed; TimeoutStopSec=15;
OOMPolicy=continue; TasksMax=infinity.
herdr-web.service: Wants= and After= herdr.service. Neither unit has Requires=/BindsTo=/PartOf=
on the other. deploy/install.sh installs BOTH as system units.
"""
from __future__ import annotations

import configparser
import os
import re
import shlex
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[3] / "deploy"


class _Multi(dict):
    """configparser dict_type that keeps every value of a repeated key (systemd allows repeats)."""
    def __setitem__(self, key, value):
        if key in self and isinstance(value, list) and isinstance(self[key], list):
            self[key].extend(value)
        else:
            super().__setitem__(key, value)


def unit(name: str) -> dict[str, dict[str, list[str]]]:
    path = DEPLOY / name
    assert path.is_file(), f"missing {path.name}"
    cp = configparser.RawConfigParser(strict=False, dict_type=_Multi, empty_lines_in_values=False,
                                      comment_prefixes=("#", ";"), inline_comment_prefixes=None)
    cp.optionxform = str
    cp.read_string(path.read_text(), source=name)
    out: dict[str, dict[str, list[str]]] = {}
    for sec in cp.sections():
        out[sec] = {}
        for k, v in cp._sections[sec].items():          # raw multi-values
            vals = v if isinstance(v, list) else [v]
            out[sec][k] = [x for x in vals if x is not None]
    return out


def one(u, sec, key) -> str:
    vals = u.get(sec, {}).get(key, [])
    assert vals, f"[{sec}] {key}= is missing"
    return vals[-1].strip().splitlines()[-1].strip()


def words(u, sec, key) -> list[str]:
    return [w for v in u.get(sec, {}).get(key, []) for w in v.split()]


def env(u) -> dict[str, str]:
    out = {}
    for v in u.get("Service", {}).get("Environment", []):
        for item in shlex.split(v):
            k, _, val = item.partition("=")
            out[k] = val
    return out


@pytest.fixture(scope="module")
def herdr():
    return unit("herdr.service")


@pytest.fixture(scope="module")
def web():
    return unit("herdr-web.service")


def test_herdr_unit_runs_as_the_service_user(herdr):
    user, group = one(herdr, "Service", "User"), one(herdr, "Service", "Group")
    assert user and user not in ("root", "0"), user
    assert group and group not in ("root", "0"), group


def test_herdr_unit_environment_is_clean(herdr):
    e = env(herdr)
    for k in ("HOME", "PATH", "SHELL", "LANG"):
        assert e.get(k), f"Environment= must set {k}"
    assert e["LANG"] == "C" and e.get("LC_CTYPE") == "C.UTF-8", "Decision 12: LANG=C, LC_CTYPE=C.UTF-8"
    assert not [k for k in e if k.startswith("HERDRWEB_")], "no HERDRWEB_* in the herdr unit"
    assert not herdr["Service"].get("EnvironmentFile"), "no EnvironmentFile: the environment must be explicit"


def test_herdr_unit_execstart_is_herdr_server(herdr):
    argv = shlex.split(re.sub(r"^[-@:+!]+(?=/)", "", one(herdr, "Service", "ExecStart")))
    assert argv[-1] == "server" and len(argv) == 2, f"ExecStart must be `<herdr> server`: {argv}"
    assert os.path.basename(argv[0]) == "herdr" or re.fullmatch(r"@[A-Z_]*HERDR[A-Z_]*@", argv[0]), \
        f"ExecStart must run the herdr binary (or an @HERDR_BIN@-style placeholder): {argv[0]}"
    assert "--handoff" not in " ".join(argv)


@pytest.mark.parametrize("key,want", [("Restart", "always"), ("KillMode", "mixed"), ("TimeoutStopSec", "15"),
                                      ("OOMPolicy", "continue"), ("TasksMax", "infinity")])
def test_herdr_unit_service_keys(herdr, key, want):
    assert one(herdr, "Service", key) == want, f"{key}= must be {want}"


def test_web_unit_wants_and_after_herdr(web):
    assert "herdr.service" in words(web, "Unit", "Wants")
    assert "herdr.service" in words(web, "Unit", "After")


@pytest.mark.parametrize("key", ["Requires", "BindsTo", "PartOf", "Requisite"])
def test_no_hard_dependency_either_way(herdr, web, key):
    assert "herdr.service" not in words(web, "Unit", key), f"herdr-web.service {key}=herdr.service"
    assert "herdr-web.service" not in words(herdr, "Unit", key), f"herdr.service {key}=herdr-web.service"


def test_units_are_system_units(herdr, web):
    for name, u in (("herdr.service", herdr), ("herdr-web.service", web)):
        text = (DEPLOY / name).read_text()
        assert "%h" not in text, f"{name}: %h is a user-manager specifier"
        assert "default.target" not in " ".join(words(u, "Install", "WantedBy")), \
            f"{name}: a system unit is wanted by multi-user.target"
        assert words(u, "Install", "WantedBy"), f"{name}: [Install] WantedBy= missing"


def test_install_sh_installs_both_as_system_units():
    text = (DEPLOY / "install.sh").read_text()
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "/etc/systemd/system" in code, "install.sh must install into /etc/systemd/system"
    assert "herdr.service" in code and "herdr-web.service" in code
    assert "systemctl --user" not in code and "systemd/user" not in code, "no user-manager units"
    assert re.search(r"systemctl\s+daemon-reload", code), "system daemon-reload"
