#!/usr/bin/env python3
"""Fake `systemctl`, `systemd-run`, `sudo` and an owner-notify command for the cutover fixture tests.

Installed as symlinks named systemctl / systemd-run / hw-notify in the scratch bin dir (dispatch on
the invoked name). Every call is appended to $FAKE_LEDGER with a snapshot of the world at that moment:
  {"tool": "systemctl", "argv": [...], "t": ..., "app_marker": <APP_DIR/backend/app/MARKER or null>,
   "unit_files": sorted(UNIT_DIR), "server_pids": [pids of live `HERDR_BIN server` processes]}
so a test can assert ORDER against state ("the web unit was stopped while the old tree was in place").

Units ($FAKE_WORLD/config.json names them) are emulated with pid files in $FAKE_WORLD/units/<unit>:
  start <web unit>     needs UNIT_DIR/<unit> (else rc 5, "Unit ... not found"); spawns web_fake.py on
                       HERDRWEB_HOST:HERDRWEB_PORT from CONF_DIR/env. The fake web refuses with 4503 when
                       the herdr socket is down only if APP_DIR holds the STAGED tree (the guard ships
                       with the new backend); with the old tree it auto-starts a server in its own unit.
  start <herdr unit>   needs UNIT_DIR/<unit>; spawns `HERDR_BIN server` (HOME=SERVICE_HOME) unless the
                       world option herdr_unit_fails is set (then the unit "fails": rc 1, nothing runs).
  stop <unit>          SIGTERM every pid of the unit, wait for them to exit.
  restart, enable [--now], disable [--now], daemon-reload, is-active, show -p MainPID --value.
`systemd-run` only records (the test asserts the detach call). `hw-notify` only records.
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(os.path.realpath(__file__)).parent


def W() -> Path:
    return Path(os.environ["FAKE_WORLD"])


def cfg() -> dict:
    return json.loads((W() / "config.json").read_text())


def server_pids(herdr_bin: str) -> list[int]:
    out = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            argv = (d / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        argv = [a.decode(errors="replace") for a in argv if a]
        for i in range(min(2, len(argv) - 1)):
            if argv[i] == herdr_bin and argv[i + 1] == "server":
                try:
                    stat = (d / "stat").read_text()
                    if stat.split(") ", 1)[1][0] == "Z":
                        break
                except OSError:
                    break
                out.append(int(d.name))
                break
    return sorted(out)


def ledger(tool: str, argv: list[str]) -> None:
    c = cfg()
    marker = Path(c["APP_DIR"], "backend", "app", "MARKER")
    entry = {"t": time.time(), "tool": tool, "argv": argv,
             "app_marker": marker.read_text() if marker.exists() else None,
             "unit_files": sorted(os.listdir(c["UNIT_DIR"])) if os.path.isdir(c["UNIT_DIR"]) else [],
             "server_pids": server_pids(c["HERDR_BIN"])}
    with open(os.environ["FAKE_LEDGER"], "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(entry) + "\n")


def unit_file(unit: str) -> Path:
    return W() / "units" / unit


def pids(unit: str) -> list[int]:
    f = unit_file(unit)
    if not f.exists():
        return []
    return [int(x) for x in f.read_text().split() if x.strip()]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] != "Z"
    except OSError:
        return False


def add_pid(unit: str, pid: int) -> None:
    f = unit_file(unit)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a") as fh:
        fh.write(f"{pid}\n")


def env_file(c: dict) -> dict:
    out = {}
    p = Path(c["CONF_DIR"], "env")
    for ln in p.read_text().splitlines():
        if "=" in ln and not ln.lstrip().startswith("#"):
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def spawn(argv: list[str], env_extra: dict) -> int:
    env = dict(os.environ, **env_extra)
    p = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=open(W() / "spawn.log", "a"), start_new_session=True)
    return p.pid


def start(unit: str) -> int:
    c = cfg()
    if not Path(c["UNIT_DIR"], unit).exists():
        print(f"Failed to start {unit}: Unit {unit} not found.", file=sys.stderr)
        return 5
    if any(alive(p) for p in pids(unit)):
        return 0
    unit_file(unit).write_text("")
    if unit == c["HERDR_UNIT"]:
        if c.get("options", {}).get("herdr_unit_fails"):
            print(f"Job for {unit} failed.", file=sys.stderr)
            return 1
        add_pid(unit, spawn([c["HERDR_BIN"], "server"], {"HOME": c["SERVICE_HOME"]}))
        return 0
    if unit in (c["WEB_UNIT"], c["OLD_WEB_UNIT"]):
        e = env_file(c)
        add_pid(unit, spawn([sys.executable, str(HERE / "web_fake.py")],
                            {"HOME": c["SERVICE_HOME"], "FAKE_WEB_HOST": e["HERDRWEB_HOST"],
                             "FAKE_WEB_PORT": e["HERDRWEB_PORT"], "FAKE_WEB_UNIT": unit}))
        port = int(e["HERDRWEB_PORT"])
        import socket
        end = time.time() + 10
        while time.time() < end:
            try:
                socket.create_connection((e["HERDRWEB_HOST"], port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.05)
        return 0
    print(f"fake systemctl: unknown unit {unit}", file=sys.stderr)
    return 5


def stop(unit: str) -> int:
    ps = [p for p in pids(unit) if alive(p)]
    for p in ps:
        try:
            os.killpg(p, signal.SIGTERM)
        except OSError:
            try:
                os.kill(p, signal.SIGTERM)
            except OSError:
                pass
    end = time.time() + 10
    while time.time() < end and any(alive(p) for p in ps):
        time.sleep(0.05)
    unit_file(unit).write_text("") if unit_file(unit).exists() else None
    return 0


def systemctl(argv: list[str]) -> int:
    args = [a for a in argv if a not in ("--no-pager", "--quiet", "-q", "--no-block")]
    if not args:
        return 0
    verb, rest = args[0], args[1:]
    now = "--now" in rest
    units = [u for u in rest if not u.startswith("-")]
    if verb == "daemon-reload":
        return 0
    if verb in ("enable", "disable"):
        rc = 0
        for u in units:
            if verb == "enable" and not Path(cfg()["UNIT_DIR"], u).exists():
                print(f"Failed to enable unit: Unit file {u} does not exist.", file=sys.stderr)
                rc = 1
            elif now:
                rc |= start(u) if verb == "enable" else stop(u)
        return rc
    if verb == "start":
        return max([start(u) for u in units] or [0])
    if verb == "stop":
        return max([stop(u) for u in units] or [0])
    if verb == "restart":
        rc = 0
        for u in units:
            stop(u)
            rc |= start(u)
        return rc
    if verb == "is-active":
        ok = all(any(alive(p) for p in pids(u)) for u in units)
        print("active" if ok else "inactive")
        return 0 if ok else 3
    if verb == "show":
        u = units[-1] if units else ""
        live = [p for p in pids(u) if alive(p)]
        if "MainPID" in " ".join(rest):
            print(live[0] if live else 0)
        return 0
    return 0


def main() -> int:
    name = os.path.basename(sys.argv[0])
    argv = sys.argv[1:]
    if name == "sudo":                                   # `sudo -n <cmd>` runs <cmd> from PATH
        while argv and argv[0].startswith("-"):
            argv = argv[1:]
        os.execvp(argv[0], argv)
    ledger(name, argv)
    if name == "systemctl":
        return systemctl(argv)
    return 0                                             # systemd-run, hw-notify: record only


if __name__ == "__main__":
    sys.exit(main())
