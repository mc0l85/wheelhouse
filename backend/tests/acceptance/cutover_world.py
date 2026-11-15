"""Offline world for the cutover fixture tests (P2, lane E). Locked with the E-F* items.

A World is a scratch host: a fake herdr server holding panes (tests/fakes/herdr_cli.py, a real
unix-socket API built on FakeHerdrApi), fake systemctl / systemd-run / sudo / notify on PATH
(tests/fakes/systemd_fake.py), a fake web (tests/fakes/web_fake.py), a deployed tree, a staged tree,
a unit dir, a herdr home and a host-env file pointing deploy/cutover.sh at all of it. Every fake call
lands in a JSONL ledger; the fake server logs every pane input and status change in its state file.
Tests assert on those RECORDS (ledger, inputs, files on disk), never on an exception propagating.

Nothing here touches the real herdr, the real units or /opt: HERDR_BIN is the fake (asserted), the
service home is a scratch dir (asserted), and systemctl/systemd-run resolve to the fakes on PATH.

Interfaces pinned by these tests (deploy/cutover.sh; see the E-F* items in prd/acceptance.json):
  deploy/cutover.sh --host-env FILE [--dry-run] [--force] [--rollback] [--foreground] [--coordinator PANE]
    exit 0 done | 2 usage/config/preflight (nothing touched) | 3 aborted before step 4 (nothing changed)
         4 failed in step 5/6 and rolled back | 5 finished, problems recorded in steps 7-9
    without --dry-run/--foreground/--rollback it only re-executes itself detached:
      systemd-run --unit=herdr-cutover ... <self> ... --foreground   (and does nothing else)
    --coordinator defaults to $HERDR_PANE_ID.
  host-env: KEY=VALUE lines (# comments, no shell expansion): SERVICE_USER SERVICE_GROUP SERVICE_HOME
    HERDR_BIN HERDR_HOME APP_DIR CONF_DIR UNIT_DIR HERDR_UNIT WEB_UNIT OLD_WEB_UNIT STAGE_DIR STATE_DIR
    UPDATE_SCRIPT HERDR_AS_USER(runuser|direct) NOTIFY_CMD(optional) HOST_NAME(live checks)
    IDLE_WAIT_S QUIT_WAIT_S RELAUNCH_WAIT_S SOCKET_WAIT_S (defaults 300 / 10 / 60 / 30).
  herdr is called as HERDR_BIN with HOME=SERVICE_HOME (runuser -u SERVICE_USER when
    HERDR_AS_USER=runuser), the rest of the environment passed through; systemctl, systemd-run by bare
    name from PATH.
  a herdr server = a process whose argv[0] or argv[1] is HERDR_BIN and the next argument is `server`,
    owned by the uid that owns HERDR_HOME.
  STATE_DIR/<run-id>/ manifest.json result.json session.json session-snapshot.json
    session.json.after-quit old-tree/{app,static,requirements.txt} old-units/<OLD_WEB_UNIT>
    old-update-script ; STATE_DIR/latest -> <run-id> (symlink). --dry-run writes nothing anywhere.
  --dry-run stdout: one JSON object {"manifest": <manifest>, "plan": [<command strings>]}.
  manifest.json: {"run_id", "started_at" (ISO 8601), "coordinator", "tab_count", "panes": [row...]}
    row: {"pane_id", "tab_id", "agent" (null for a shell), "agent_status",
          "session": {"kind", "value"} | null, "pid" (agent pid | null), "argv" (foreground argv | null),
          "proc_cmdline" (/proc/<pid>/cmdline as a list | null), "coordinator": bool,
          "action": "resume" | "fresh" | "none", "relaunch": [argv] | null}
    resume = the session has a transcript (claude: SERVICE_HOME/.claude/projects/*/<uuid>.jsonl;
    pi: the session path exists); fresh = it has none (no prompt was ever sent).
    relaunch argv = [argv0] + the original flags without --resume/-r/--session <v>, --continue/-c,
    + ["--resume", uuid] (claude) / ["--session", path] (pi); fresh = without the session flag.
    argv0 = the foreground argv[0] when its basename is the agent, else the agent name.
  result.json: {"run_id", "started_at", "outcome": "done"|"aborted"|"rolled_back"|"problems",
    "abort_reason"?, "steps": [{"step", "ok", ...}], "guard": {"code", "server_appeared"},
    "agents": [{"pane_id", "relaunched" (bool), "native" (bool), "session_match" (bool),
                "resume_sent" (bool), "skipped" (reason | null)}],
    "web_restart": {"server_pid_before", "server_pid_after"}}
"""
from __future__ import annotations

import getpass
import grp
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from tests.acceptance.conftest import _ensure_loopback
from tests.conftest import FAKES, safety_violations

REPO = Path(__file__).resolve().parents[3]
CUTOVER = REPO / "deploy" / "cutover.sh"
UPDATE_SH = REPO / "deploy" / "herdr-update.sh"

OLD_UNIT_TEXT = "[Unit]\nDescription=old herdr web (in-cgroup server)\n[Service]\nExecStart=/bin/true\n"
OLD_UPDATE_TEXT = "#!/bin/bash\n# old nightly update\nherdr update --handoff\n"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def tree_digest(root: Path) -> dict[str, str]:
    out = {}
    if root.is_file():
        return {"": hashlib.sha256(root.read_bytes()).hexdigest()}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class World:
    """One scratch host. Build with panes, boot(), run the cutover, read the records, close()."""

    def __init__(self, panes: list[dict] | None = None, options: dict | None = None,
                 coordinator: str = "wf:p2", timings: dict | None = None) -> None:
        _ensure_loopback()                               # prd_check runs checks in an empty netns
        base = os.environ.get("HW_CUTOVER_TMP") or "/tmp"
        self.root = Path(tempfile.mkdtemp(prefix="hwc-", dir=base))   # short: unix socket paths <= 107
        r = self.root
        self.bin, self.home, self.app = r / "bin", r / "home", r / "app"
        self.conf, self.units, self.stage, self.state = r / "conf", r / "units", r / "stage", r / "state"
        self.world, self.ledger_path, self.host_env = r / "world", r / "ledger.jsonl", r / "host.env"
        self.herdr_home = self.home / ".config" / "herdr"
        self.update_script = self.home / ".local" / "bin" / "herdr-update.sh"
        self.coordinator = coordinator
        self.options = options or {}
        self.sleepers: list[subprocess.Popen] = []
        self.staged_marker = f"staged-{uuid.uuid4()}"
        self.port = free_port()
        self.timings = {"IDLE_WAIT_S": "3", "QUIT_WAIT_S": "3", "RELAUNCH_WAIT_S": "6", "SOCKET_WAIT_S": "8",
                        **(timings or {})}
        self.panes = panes if panes is not None else self.default_panes()
        self._layout()

    # ---- construction -----------------------------------------------------------------------
    def claude_transcript(self, sid: str) -> Path:
        p = self.home / ".claude" / "projects" / "-work-proj" / f"{sid}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n")
        return p

    def pi_session(self, name: str = "s1") -> str:
        p = self.home / ".pi" / "agent" / "sessions" / "--work-proj--" / f"{name}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"type":"session"}\n')
        return str(p)

    def agent(self, pane_id, tab_id, agent, sid=None, *, argv=None, status="idle", transcript=True, **kw) -> dict:
        """An agent pane. sid=None -> no agent_session at all (e.g. stuck on a trust dialog)."""
        sess = None
        if sid is not None:
            kind = "id" if agent == "claude" else "path"
            sess = {"agent": agent, "kind": kind, "source": f"herdr:{agent}", "value": sid}
            if transcript and agent == "claude":
                self.claude_transcript(sid)
        return {"pane_id": pane_id, "tab_id": tab_id, "workspace_id": "wf", "cwd": "/work/proj",
                "agent": agent, "agent_status": status, "agent_session": sess,
                "fg_argv": argv or [agent], "spawn_sleeper": True, **kw}

    def shell(self, pane_id, tab_id) -> dict:
        return {"pane_id": pane_id, "tab_id": tab_id, "workspace_id": "wf", "cwd": "/work/proj",
                "agent": None, "agent_status": "unknown", "agent_session": None, "fg_argv": None}

    def default_panes(self) -> list[dict]:
        self.U1, self.U2, self.U4 = (str(uuid.uuid4()) for _ in range(3))
        self.PI = self.pi_session()
        return [
            self.agent("wf:p1", "wf:t1", "claude", self.U1,
                       argv=["/x/bin/claude", "--dangerously-skip-permissions", "--resume", "stale-id"]),
            self.agent("wf:p2", "wf:t2", "claude", self.U2, argv=["/x/bin/claude", "--resume", self.U2]),
            self.agent("wf:p3", "wf:t3", "pi", self.PI, argv=["/x/pi/bin/pi"]),
            self.agent("wf:p4", "wf:t4", "claude", self.U4, argv=["/x/bin/claude", "--model", "opus"],
                       transcript=False),
            self.shell("wf:p5", "wf:t4"),
        ]

    def _layout(self) -> None:
        for d in (self.bin, self.home, self.conf, self.units, self.state, self.world / "units", self.herdr_home):
            d.mkdir(parents=True, exist_ok=True)
        (self.bin / "herdr").symlink_to(FAKES / "herdr_cli.py")
        for name in ("systemctl", "systemd-run", "sudo", "hw-notify"):
            (self.bin / name).symlink_to(FAKES / "systemd_fake.py")
        bad = safety_violations(str(self.bin / "herdr"), str(self.herdr_home))
        assert not bad, bad
        # deployed (old) tree
        b = self.app / "backend"
        (b / "app").mkdir(parents=True)
        (b / "app" / "MARKER").write_text("old")
        (b / "app" / "main.py").write_text("# old backend\n")
        (b / "static" / "assets").mkdir(parents=True)
        (b / "static" / "index.html").write_text("<html>old</html>")
        (b / "static" / "assets" / "old.js").write_text("old()")
        (b / "requirements.txt").write_text("fastapi==0.1\n")
        (b / ".venv" / "bin").mkdir(parents=True)
        (b / ".venv" / "bin" / "uvicorn").write_text("#venv\n")
        (b / ".env").write_text("HERDRWEB_ENABLE_WRITES=1\n")
        # staged (new) tree
        s = self.stage / "backend"
        (s / "app").mkdir(parents=True)
        (s / "app" / "MARKER").write_text(self.staged_marker)
        (s / "app" / "main.py").write_text("# new backend\n")
        (s / "static" / "assets").mkdir(parents=True)
        (s / "static" / "index.html").write_text("<html>new</html>")
        (s / "static" / "assets" / "new.js").write_text("new()")
        (s / "requirements.txt").write_text("fastapi==0.2\n")
        (self.stage / "REVISION").write_text("0" * 40 + "\n")
        # web env, old unit, old nightly script
        (self.conf / "env").write_text(f"HERDRWEB_HOST=127.0.0.1\nHERDRWEB_PORT={self.port}\nHERDR_SESSION=default\n")
        (self.units / "herdr-web-api.service").write_text(OLD_UNIT_TEXT)
        self.update_script.parent.mkdir(parents=True, exist_ok=True)
        self.update_script.write_text(OLD_UPDATE_TEXT)
        self.update_script.chmod(0o755)
        user = getpass.getuser()
        values = {
            "SERVICE_USER": user, "SERVICE_GROUP": grp.getgrgid(os.getgid()).gr_name,
            "SERVICE_HOME": str(self.home), "HERDR_BIN": str(self.bin / "herdr"),
            "HERDR_HOME": str(self.herdr_home), "APP_DIR": str(self.app), "CONF_DIR": str(self.conf),
            "UNIT_DIR": str(self.units), "HERDR_UNIT": "herdr.service", "WEB_UNIT": "herdr-web.service",
            "OLD_WEB_UNIT": "herdr-web-api.service", "STAGE_DIR": str(self.stage),
            "STATE_DIR": str(self.state), "UPDATE_SCRIPT": str(self.update_script),
            "HERDR_AS_USER": "direct", "NOTIFY_CMD": str(self.bin / "hw-notify"),
            "HOST_NAME": socket.gethostname(), **self.timings,
        }
        self.values = values
        self.host_env.write_text("# scratch host values (cutover fixture test)\n"
                                 + "".join(f"{k}={v}\n" for k, v in values.items()))
        (self.world / "config.json").write_text(json.dumps({**values, "STAGED_MARKER": self.staged_marker,
                                                            "options": self.options}))

    # ---- lifecycle ------------------------------------------------------------------------------
    def boot(self) -> "World":
        """Old layout: the herdr server and the web both run inside OLD_WEB_UNIT."""
        for p in self.panes:
            if p.pop("spawn_sleeper", False):
                sp = subprocess.Popen(["sleep", "600"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
                self.sleepers.append(sp)
                p["pid"] = sp.pid
            p.setdefault("shell_pid", os.getpid())
        (self.world / "state.json").write_text(json.dumps({"panes": self.panes, "options": self.options}))
        env = self.env()
        srv = subprocess.Popen([str(self.bin / "herdr"), "server"], env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        (self.world / "units" / "herdr-web-api.service").write_text(f"{srv.pid}\n")
        self.old_server_pid = srv.pid
        self.wait_socket(True)
        web = subprocess.Popen([sys.executable, str(FAKES / "web_fake.py")],
                               env={**env, "FAKE_WEB_HOST": "127.0.0.1", "FAKE_WEB_PORT": str(self.port),
                                    "FAKE_WEB_UNIT": "herdr-web-api.service"},
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
        with open(self.world / "units" / "herdr-web-api.service", "a") as f:
            f.write(f"{web.pid}\n")
        end = time.time() + 10
        while time.time() < end:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.3).close()
                break
            except OSError:
                time.sleep(0.05)
        self.ledger_path.write_text("")                  # the boot is not part of the record
        self.before = self.snapshot()
        return self

    def env(self, **extra) -> dict:
        e = dict(os.environ)
        e.update({"PATH": f"{self.bin}:{e.get('PATH', '/usr/bin:/bin')}", "FAKE_LEDGER": str(self.ledger_path),
                  "FAKE_WORLD": str(self.world), "HOME": str(self.home), "HERDR_PANE_ID": self.coordinator})
        e.pop("HERDR_SOCKET_PATH", None)
        e.update(extra)
        return e

    def wait_socket(self, up: bool, timeout: float = 10) -> bool:
        path = str(self.herdr_home / "herdr.sock")
        end = time.time() + timeout
        while time.time() < end:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.connect(path)
                ok = True
            except OSError:
                ok = False
            finally:
                s.close()
            if ok == up:
                return True
            time.sleep(0.05)
        return False

    def run(self, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
        return subprocess.run([str(CUTOVER), "--host-env", str(self.host_env), *args], env=self.env(),
                              capture_output=True, text=True, timeout=timeout, cwd=str(REPO))

    def close(self) -> None:
        for f in (self.world / "units").glob("*"):
            for x in f.read_text().split():
                try:
                    os.killpg(int(x), signal.SIGKILL)
                except OSError:
                    try:
                        os.kill(int(x), signal.SIGKILL)
                    except OSError:
                        pass
        for pid in self.server_pids():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        for sp in self.sleepers:
            sp.kill()
            sp.wait()
        shutil.rmtree(self.root, ignore_errors=True)

    # ---- records ----------------------------------------------------------------------------------
    def ledger(self) -> list[dict]:
        return [json.loads(ln) for ln in self.ledger_path.read_text().splitlines() if ln.strip()]

    def calls(self, tool: str, verb: str | None = None) -> list[dict]:
        out = [e for e in self.ledger() if e["tool"] == tool]
        if verb is not None:
            out = [e for e in out if e["argv"][:1] == [verb] or verb in e["argv"][:2]]
        return out

    def systemctl(self, verb: str, unit: str) -> list[dict]:
        return [e for e in self.calls("systemctl") if e["argv"] and verb in e["argv"] and unit in e["argv"]]

    def herdr_calls(self) -> list[list[str]]:
        return [e["argv"] for e in self.calls("herdr")]

    def server_state(self) -> dict:
        return json.loads((self.world / "state.json").read_text())

    def inputs(self, pane_id: str) -> list[dict]:
        return self.server_state().get("inputs", {}).get(pane_id, [])

    def status_log(self, pane_id: str) -> list[list]:
        return self.server_state().get("status_log", {}).get(pane_id, [])

    def run_dirs(self) -> list[Path]:
        return sorted(p for p in self.state.iterdir() if p.is_dir() and not p.is_symlink()) if self.state.exists() else []

    def latest(self) -> Path:
        link = self.state / "latest"
        assert link.is_symlink(), f"STATE_DIR/latest is not a symlink: {sorted(os.listdir(self.state))}"
        return link.resolve()

    def manifest(self) -> dict:
        return json.loads((self.latest() / "manifest.json").read_text())

    def result(self) -> dict:
        return json.loads((self.latest() / "result.json").read_text())

    def server_pids(self) -> list[int]:
        herdr_bin = str(self.bin / "herdr")
        out = []
        for d in Path("/proc").iterdir():
            if not d.name.isdigit():
                continue
            try:
                argv = [a.decode() for a in (d / "cmdline").read_bytes().split(b"\0") if a]
                stat = (d / "stat").read_text()
            except OSError:
                continue
            if stat.split(") ", 1)[1][0] == "Z":
                continue
            if any(argv[i] == herdr_bin and argv[i + 1] == "server" for i in range(min(2, len(argv) - 1))):
                out.append(int(d.name))
        return out

    def snapshot(self) -> dict:
        """Everything a cutover may change, by content."""
        return {
            "app": tree_digest(self.app / "backend"),
            "units": tree_digest(self.units),
            "update": tree_digest(self.update_script),
            "session": tree_digest(self.herdr_home / "session.json") if (self.herdr_home / "session.json").exists() else {},
            "state_dir": sorted(os.listdir(self.state)),
        }
