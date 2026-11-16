#!/usr/bin/env python3
"""The engine behind deploy/cutover.sh (stdlib only; the host's system python3 runs it, as root).

deploy/cutover.sh is a thin launcher; read its header for the CLI and the exit codes. The contract
(host-env keys, state layout, manifest and result.json schemas) is pinned in
backend/tests/acceptance/cutover_world.py.

Safety rules this file keeps:
  * steps 1-3 change nothing on the host except quitting agents (and the run's own state dir);
  * nothing is deleted: the old tree, the old unit and the old nightly script go into the run dir, and a
    rollback moves the new files aside into the run dir too;
  * every destructive action first records its intent in <run>/progress.json, so a rollback (automatic
    or `--rollback`) undoes exactly what was started, even after a crash;
  * herdr is only ever called through HERDR_BIN with the service user's HOME.
"""
from __future__ import annotations

import base64
import fcntl
import glob
import json
import os
import pwd
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = {"web": "herdr-web.service", "herdr": "herdr.service"}
REQUIRED = ("SERVICE_USER", "SERVICE_GROUP", "SERVICE_HOME", "HERDR_BIN", "HERDR_HOME", "APP_DIR", "CONF_DIR",
            "UNIT_DIR", "HERDR_UNIT", "WEB_UNIT", "OLD_WEB_UNIT", "STAGE_DIR", "STATE_DIR", "UPDATE_SCRIPT",
            "HERDR_AS_USER")
OPTIONAL = ("NOTIFY_CMD", "HOST_NAME", "IDLE_WAIT_S", "QUIT_WAIT_S", "RELAUNCH_WAIT_S", "SOCKET_WAIT_S")
DEFAULTS = {"IDLE_WAIT_S": "300", "QUIT_WAIT_S": "10", "RELAUNCH_WAIT_S": "60", "SOCKET_WAIT_S": "30"}
WEB_ENV_KEYS = ("HERDRWEB_HOST", "HERDRWEB_PORT", "HERDR_SESSION")
EXIT = {"done": 0, "problems": 5, "aborted": 3, "rolled_back": 4}


class Usage(Exception):
    """Exit 2: bad arguments, host values or preflight; nothing was touched."""


class Abort(Exception):
    """Exit 3: stop before step 4; nothing on the host changed."""


class StepFailed(Exception):
    """A failure in steps 4-6: roll back."""


def log(msg: str) -> None:
    print(f"cutover: {msg}", file=sys.stderr, flush=True)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "+00:00"


# ---------------------------------------------------------------------------------------- host values

def load_host_env(path: str) -> dict:
    """KEY=VALUE lines, `#` comments, no shell expansion. Unknown or missing required keys are refused."""
    try:
        text = Path(path).read_text()
    except OSError as e:
        raise Usage(f"cannot read host env {path}: {e}")
    vals: dict[str, str] = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise Usage(f"{path}:{n}: not KEY=VALUE")
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k not in REQUIRED and k not in OPTIONAL:
            raise Usage(f"{path}:{n}: unknown key {k}")
        vals[k] = v
    missing = [k for k in REQUIRED if not vals.get(k)]
    if missing:
        raise Usage("host env is missing: " + ", ".join(missing))
    for k, v in DEFAULTS.items():
        vals.setdefault(k, v)
    for k in DEFAULTS:
        try:
            if float(vals[k]) < 0:
                raise ValueError
        except ValueError:
            raise Usage(f"{k} must be a number of seconds")
    if vals["HERDR_AS_USER"] not in ("runuser", "direct"):
        raise Usage("HERDR_AS_USER must be runuser or direct")
    for k in ("SERVICE_HOME", "HERDR_BIN", "HERDR_HOME", "APP_DIR", "CONF_DIR", "UNIT_DIR", "STAGE_DIR",
              "STATE_DIR", "UPDATE_SCRIPT"):
        if not os.path.isabs(vals[k]):
            raise Usage(f"{k} must be an absolute path")
        if any(c in vals[k] for c in " \t\n|&\\"):
            raise Usage(f"{k} must not contain whitespace, | & or a backslash")
    for k in ("HERDR_UNIT", "WEB_UNIT", "OLD_WEB_UNIT"):
        if not vals[k].endswith(".service") or "/" in vals[k]:
            raise Usage(f"{k} must be a plain <name>.service")
    if len({vals["HERDR_UNIT"], vals["WEB_UNIT"], vals["OLD_WEB_UNIT"]}) != 3:
        raise Usage("HERDR_UNIT, WEB_UNIT and OLD_WEB_UNIT must differ")
    return vals


def read_env_file(path: Path) -> dict:
    out: dict[str, str] = {}
    for ln in path.read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


# ---------------------------------------------------------------------------------------- files

def atomic_write(path: Path, data: bytes, mode: int, like: os.stat_result | None = None,
                 owner: tuple[int, int] | None = None) -> None:
    tmp = path.with_name("." + path.name + ".cutover-tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    if owner is None and like is not None:
        owner = (like.st_uid, like.st_gid)
    if owner is not None and os.geteuid() == 0:
        os.chown(tmp, *owner)
    os.replace(tmp, path)


def chown_tree(path: Path, uid: int, gid: int) -> None:
    if os.geteuid() != 0:
        return
    os.lchown(path, uid, gid)
    if path.is_dir() and not path.is_symlink():
        for dp, dns, fns in os.walk(path):
            for n in dns + fns:
                os.lchown(os.path.join(dp, n), uid, gid)


def move_file(src: Path, dst: Path) -> None:
    """Rename; across filesystems copy, verify and only then unlink the source (single files only)."""
    try:
        os.rename(src, dst)
    except OSError:
        shutil.copy2(src, dst)
        if dst.read_bytes() != src.read_bytes():
            raise OSError(f"copy of {src} to {dst} does not verify")
        os.unlink(src)


def same_fs(a: str, b: str) -> bool:
    return os.stat(a).st_dev == os.stat(b).st_dev


def tab_count(session_json: Path) -> int:
    sess = json.loads(session_json.read_text())
    return sum(len(w.get("tabs", [])) for w in sess.get("workspaces", []))


# ---------------------------------------------------------------------------------------- relaunch argv

BOOL_FLAGS = {"--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "--verbose", "--debug",
              "--ide", "--strict-mcp-config", "--no-session-persistence", "--no-session", "--offline", "--no-tools",
              "--continue", "-c", "-v", "--version"}
SESSION_FLAGS = ("--resume", "-r", "--session")


def agent_index(argv: list | None, agent: str) -> int | None:
    """Index of the first argv element whose basename is the agent (a node-run agent has it at 1+)."""
    for i, a in enumerate(argv or []):
        if os.path.basename(str(a)) == agent:
            return i
    return None


def relaunch_argv(agent: str, session: dict | None, fg_argv: list | None, resumable: bool) -> list:
    """[agent path] + the flags after it + --resume <uuid> / --session <path> (none when fresh).

    Whatever precedes the agent element (an interpreter) is dropped, session flags and --continue are
    replaced, and positional prompt arguments are dropped. A word right after a flag is that flag's value
    unless the flag is a known boolean; a word anywhere else is a prompt."""
    idx = agent_index(fg_argv, agent)
    cmd = str(fg_argv[idx]) if idx is not None else agent
    rest: list[str] = []
    args = [str(a) for a in fg_argv[idx + 1:]] if idx is not None else []
    i = 0
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else None
        takes_value = nxt is not None and not nxt.startswith("-")
        if a in SESSION_FLAGS:
            i += 2 if takes_value else 1                      # never eat the next flag
            continue
        if a.startswith("--resume=") or a.startswith("--session=") or a in ("--continue", "-c"):
            i += 1
            continue
        if a.startswith("-"):
            rest.append(a)
            if "=" not in a and a not in BOOL_FLAGS and takes_value:
                rest.append(nxt)
                i += 1
            i += 1
            continue
        i += 1                                                # positional: a prompt, dropped
    out = [cmd] + rest
    if resumable and session:
        out += ["--resume" if agent == "claude" else "--session", session["value"]]
    return out


def has_transcript(agent: str, session: dict | None, service_home: str) -> bool:
    if not session or not session.get("value"):
        return False
    val = str(session["value"])
    if agent == "claude":
        if not all(c.isalnum() or c in "-_" for c in val):
            return False
        return bool(glob.glob(os.path.join(glob.escape(service_home), ".claude", "projects", "*",
                                           glob.escape(val) + ".jsonl")))
    if session.get("kind") == "path":
        return os.path.exists(val)
    return False


# ---------------------------------------------------------------------------------------- websocket probe

def ws_attach_close_code(host: str, port: int, host_header: str, timeout: float = 6.0):
    """Open /api/ws/terminal (Host + same Origin). Returns (http_status, close_code | None)."""
    s = socket.create_connection((host, port), timeout=timeout)
    s.settimeout(timeout)
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET /api/ws/terminal?rows=24&cols=80 HTTP/1.1\r\nHost: {host_header}\r\n"
                   f"Origin: http://{host_header}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                return 0, None
            buf += chunk
        head, buf = buf.split(b"\r\n\r\n", 1)
        parts = head.split(b"\r\n", 1)[0].split(b" ")
        status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        if status != 101:
            return status, None
        end = time.time() + timeout
        while time.time() < end:
            while len(buf) >= 2:
                op, ln = buf[0] & 0x0F, buf[1] & 0x7F
                off = 2
                if ln == 126:
                    if len(buf) < 4:
                        break
                    ln, off = int.from_bytes(buf[2:4], "big"), 4
                elif ln == 127:
                    if len(buf) < 10:
                        break
                    ln, off = int.from_bytes(buf[2:10], "big"), 10
                if len(buf) < off + ln:
                    break
                payload, buf = buf[off:off + ln], buf[off + ln:]
                if op == 8:
                    return 101, (int.from_bytes(payload[:2], "big") if len(payload) >= 2 else 1005)
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                return 101, None
            buf += chunk
        return 101, None
    finally:
        s.close()


# ---------------------------------------------------------------------------------------- the run

class Cutover:
    def __init__(self, vals: dict, args) -> None:
        self.v = vals
        self.args = args
        self.home = vals["SERVICE_HOME"]
        self.herdr_bin = vals["HERDR_BIN"]
        self.herdr_home = Path(vals["HERDR_HOME"])
        self.app = Path(vals["APP_DIR"])
        self.backend = self.app / "backend"
        self.conf = Path(vals["CONF_DIR"])
        self.units = Path(vals["UNIT_DIR"])
        self.stage = Path(vals["STAGE_DIR"])
        self.state = Path(vals["STATE_DIR"])
        self.update_script = Path(vals["UPDATE_SCRIPT"])
        self.t_idle = float(vals["IDLE_WAIT_S"])
        self.t_quit = float(vals["QUIT_WAIT_S"])
        self.t_relaunch = float(vals["RELAUNCH_WAIT_S"])
        self.t_socket = float(vals["SOCKET_WAIT_S"])
        self.run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{os.getpid()}"
        self.run_dir = self.state / self.run_id
        self.progress: dict = {}
        self.result: dict = {"run_id": self.run_id, "started_at": now_iso(), "outcome": None, "steps": [],
                             "agents": []}
        self.manifest: dict = {}
        self.problems: list[str] = []
        self.quit_how: dict[str, str] = {}
        self.lock_fd = None

    # ---- process helpers -------------------------------------------------------------------
    def herdr_cmd(self, *argv: str) -> list[str]:
        base = [self.herdr_bin, *argv]
        if self.v["HERDR_AS_USER"] == "runuser":
            pre = ["runuser", "-u", self.v["SERVICE_USER"], "--"] if os.geteuid() == 0 else \
                ["sudo", "-n", "-u", self.v["SERVICE_USER"], "--"]
            return pre + ["env", f"HOME={self.home}"] + base
        return base

    def herdr_env(self) -> dict:
        e = {k: v for k, v in os.environ.items() if k not in ("HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH")}
        e["HOME"] = self.home
        return e

    def herdr(self, *argv: str, timeout: float = 30) -> dict:
        """Run a herdr CLI verb; returns its `result`. Raises RuntimeError on any failure."""
        try:
            p = subprocess.run(self.herdr_cmd(*argv), env=self.herdr_env(), capture_output=True, text=True,
                               timeout=timeout, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"herdr {' '.join(argv[:2])}: {e}")
        try:
            doc = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
        except ValueError:
            doc = {}
        if p.returncode != 0 or (isinstance(doc, dict) and doc.get("error")):
            err = doc.get("error") if isinstance(doc, dict) else None
            raise RuntimeError(f"herdr {' '.join(argv[:2])} failed: {err or p.stderr.strip() or p.returncode}")
        return (doc.get("result", doc) or {}) if isinstance(doc, dict) else {}

    def privileged(self, *argv: str) -> list[str]:
        return list(argv) if os.geteuid() == 0 else ["sudo", "-n", *argv]

    def systemctl(self, *argv: str, check: bool = True) -> int:
        log("systemctl " + " ".join(argv))
        p = subprocess.run(self.privileged("systemctl", *argv), capture_output=True, text=True, timeout=120,
                           stdin=subprocess.DEVNULL)
        if p.returncode != 0 and check:
            raise StepFailed(f"systemctl {' '.join(argv)} failed ({p.returncode}): {p.stderr.strip()}")
        return p.returncode

    def panes(self) -> list[dict]:
        return self.herdr("pane", "list").get("panes", [])

    def agents(self) -> list[dict]:
        return self.herdr("agent", "list").get("agents", [])

    def pane(self, pane_id: str) -> dict | None:
        return next((p for p in self.panes() if p["pane_id"] == pane_id), None)

    def has_agent(self, pane_id: str) -> bool:
        try:
            p = self.pane(pane_id)
        except RuntimeError:
            return False
        return bool(p and p.get("agent"))

    def server_pids(self) -> list[int]:
        try:
            owner = os.stat(self.herdr_home).st_uid
        except OSError:
            owner = None
        out = []
        for d in Path("/proc").iterdir():
            if not d.name.isdigit():
                continue
            try:
                argv = [a.decode(errors="replace") for a in (d / "cmdline").read_bytes().split(b"\0") if a]
                stat = (d / "stat").read_text()
                uid = os.stat(d).st_uid
            except OSError:
                continue
            if owner is not None and uid != owner:
                continue
            if stat.split(") ", 1)[1][0] == "Z":
                continue
            if any(argv[i] == self.herdr_bin and argv[i + 1:] == ["server"] for i in range(min(2, len(argv)))):
                out.append(int(d.name))
        return sorted(out)

    def find_orphans(self, rows: list[dict]) -> list[dict]:
        """Processes in the legacy web unit's cgroup that stopping it will kill but that are not a herdr
        server, a pane shell/agent or one of their descendants (e.g. a tmux'd `claude remote-control`)."""
        procs: list[int] = []
        unit = self.v["OLD_WEB_UNIT"]
        for base in (f"/sys/fs/cgroup/system.slice/{unit}", f"/sys/fs/cgroup/systemd/system.slice/{unit}"):
            try:
                procs = [int(x) for x in Path(base, "cgroup.procs").read_text().split()]
                break
            except (OSError, ValueError):
                continue
        if not procs:
            return []
        ppid: dict[int, int] = {}
        for d in Path("/proc").iterdir():
            if d.name.isdigit():
                try:
                    ppid[int(d.name)] = int((d / "stat").read_text().rsplit(") ", 1)[1].split()[1])
                except (OSError, ValueError, IndexError):
                    pass

        def subtree(roots: set[int]) -> set[int]:
            seen, grew = set(roots), True
            while grew:
                grew = False
                for pid, pp in ppid.items():
                    if pp in seen and pid not in seen:
                        seen.add(pid)
                        grew = True
            return seen
        roots = set(self.server_pids())
        for r in rows:
            if r.get("pid"):
                roots.add(int(r["pid"]))
            if r.get("shell_pid"):
                roots.add(int(r["shell_pid"]))
        known = subtree(roots)
        out = []
        for pid in procs:
            if pid in known:
                continue
            try:
                argv = [a.decode(errors="replace") for a in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if a]
            except OSError:
                continue
            if not argv or any("uvicorn" in a for a in argv) or argv[0] == self.herdr_bin:
                continue                                   # the web app itself and its herdr clients
            out.append({"pid": pid, "argv": argv})
        return out

    def socket_refuses(self) -> bool:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1)
        try:
            s.connect(str(self.herdr_home / "herdr.sock"))
            return False
        except OSError:
            return True
        finally:
            s.close()

    def wait_for(self, cond, timeout: float, step: float = 0.1) -> bool:
        end = time.time() + timeout
        while True:
            if cond():
                return True
            if time.time() >= end:
                return False
            time.sleep(step)

    def web_addr(self) -> tuple[str, int, str]:
        e = read_env_file(self.conf / "env")
        host = e["HERDRWEB_HOST"]
        port = int(e["HERDRWEB_PORT"])
        conn = {"0.0.0.0": "127.0.0.1", "": "127.0.0.1", "::": "::1"}.get(host, host)
        return conn, port, host

    def web_up(self) -> bool:
        conn, port, _ = self.web_addr()
        try:
            socket.create_connection((conn, port), timeout=0.5).close()
            return True
        except OSError:
            return False

    # ---- state files -----------------------------------------------------------------------
    def save_json(self, name: str, doc: dict) -> None:
        p = self.run_dir / name
        tmp = p.with_name("." + name + ".tmp")
        tmp.write_text(json.dumps(doc, indent=1))
        os.replace(tmp, p)

    def mark(self, **flags) -> None:
        """Record intent BEFORE the action it describes."""
        self.progress.update(flags)
        self.save_json("progress.json", self.progress)

    def step(self, n: int, ok: bool = True, **extra) -> None:
        self.result["steps"].append({"step": n, "ok": ok, **extra})
        if self.run_dir.exists():
            self.save_json("result.json", self.result)

    def problem(self, msg: str) -> None:
        log("PROBLEM: " + msg)
        self.problems.append(msg)

    # ---- preflight -------------------------------------------------------------------------
    def preflight_common(self) -> None:
        v = self.v
        try:
            v["SERVICE_UID"] = str(pwd.getpwnam(v["SERVICE_USER"]).pw_uid)
        except KeyError:
            raise Usage(f"SERVICE_USER {v['SERVICE_USER']} does not exist")
        for k in ("APP_DIR", "UNIT_DIR", "STATE_DIR", "HERDR_HOME"):
            if not os.path.isdir(v[k]):
                raise Usage(f"{k} is not a directory: {v[k]}")
        for k in ("UNIT_DIR", "STATE_DIR", "APP_DIR"):
            if not os.access(v[k], os.W_OK):
                raise Usage(f"{k} is not writable by this user (run as root): {v[k]}")

    def preflight(self) -> None:
        v = self.v
        self.preflight_common()
        if not os.access(self.herdr_bin, os.X_OK):
            raise Usage(f"HERDR_BIN is not executable: {self.herdr_bin}")
        for k in ("SERVICE_HOME", "CONF_DIR"):
            if not os.path.isdir(v[k]):
                raise Usage(f"{k} is not a directory: {v[k]}")
        if not self.backend.is_dir():
            raise Usage(f"{self.backend} does not exist")
        if not same_fs(v["STATE_DIR"], str(self.backend)):
            raise Usage("STATE_DIR must be on the same filesystem as APP_DIR/backend: the old tree is moved "
                        "into the run dir, never copied and deleted")
        if not self.update_script.parent.is_dir() or not os.access(self.update_script.parent, os.W_OK):
            raise Usage(f"the directory of UPDATE_SCRIPT is not writable: {self.update_script.parent}")
        for name in (*TEMPLATES.values(), "herdr-update.sh"):
            if not (HERE / name).is_file():
                raise Usage(f"missing deploy/{name}")
        if not (self.units / v["OLD_WEB_UNIT"]).is_file():
            raise Usage(f"the legacy web unit file is not in UNIT_DIR: {self.units / v['OLD_WEB_UNIT']}")
        for u in (v["WEB_UNIT"], v["HERDR_UNIT"]):
            if (self.units / u).exists():
                raise Usage(f"{u} already exists in UNIT_DIR: this host is already cut over (use --rollback)")
        env = self.conf / "env"
        if not env.is_file():
            raise Usage(f"web env file missing: {env}")
        keys = read_env_file(env)
        missing = [k for k in WEB_ENV_KEYS if not keys.get(k)]
        if missing:
            raise Usage(f"{env} lacks required keys (names only are checked): {', '.join(missing)}")
        if not keys.get("HERDR_BIN"):
            log("warning: the web env file has no HERDR_BIN; the web unit finds herdr through its PATH")
        st = self.stage
        for rel in ("backend/app", "backend/static"):
            if not (st / rel).is_dir():
                raise Usage(f"staged tree incomplete: {st / rel}")
        for rel in ("backend/requirements.txt", "REVISION", "backend/app/main.py", "backend/static/index.html"):
            if not (st / rel).is_file():
                raise Usage(f"staged tree incomplete: {st / rel}")
        for key in TEMPLATES:                                     # rendering must leave no placeholder
            try:
                self.render(key)
            except StepFailed as e:
                raise Usage(str(e))

    def preflight_rollback(self) -> None:
        """A rollback needs the host values and a run to undo, not a staged tree or free unit names."""
        self.preflight_common()
        if not (self.state / "latest").is_symlink():
            raise Usage("no cutover run to roll back (STATE_DIR/latest is missing)")

    def render(self, key: str) -> str:
        text = (HERE / TEMPLATES[key]).read_text()
        v = self.v
        for ph, val in (("@APP_DIR@", v["APP_DIR"]), ("@CONF_DIR@", v["CONF_DIR"]), ("@HERDR_BIN@", v["HERDR_BIN"]),
                        ("@SERVICE_USER@", v["SERVICE_USER"]), ("@SERVICE_GROUP@", v["SERVICE_GROUP"]),
                        ("@SERVICE_UID@", v["SERVICE_UID"]), ("@SERVICE_HOME@", v["SERVICE_HOME"])):
            text = text.replace(ph, val)
        if "@" in text.replace("@.service", ""):
            raise StepFailed(f"unrendered placeholder left in {TEMPLATES[key]}")
        return text

    # ---- step 1: manifest ------------------------------------------------------------------
    def build_manifest(self, write: bool) -> dict:
        try:
            panes = self.panes()
            agent_rows = {a["pane_id"]: a for a in self.agents()}
        except RuntimeError as e:
            raise Abort(f"cannot list panes/agents: {e}")
        coordinator = self.args.coordinator or None
        if coordinator and coordinator not in {p["pane_id"] for p in panes}:
            raise Abort(f"coordinator pane {coordinator} is not a pane of the running herdr server")
        sj = self.herdr_home / "session.json"
        try:
            tabs = tab_count(sj)
        except (OSError, ValueError) as e:
            raise Abort(f"session.json unreadable: {e}")
        rows, no_session = [], []
        for p in panes:
            arow = agent_rows.get(p["pane_id"]) or {}
            ag = p.get("agent") or arow.get("agent")
            row = {"pane_id": p["pane_id"], "tab_id": p.get("tab_id"), "agent": ag or None,
                   "agent_status": arow.get("agent_status") or p.get("agent_status"),
                   "session": None, "pid": None, "argv": None, "proc_cmdline": None,
                   "coordinator": p["pane_id"] == coordinator, "action": "none", "relaunch": None}
            if ag:
                sref = p.get("agent_session") or arow.get("agent_session")
                if not sref or not sref.get("value"):
                    no_session.append(p["pane_id"])
                else:
                    row["session"] = {"kind": sref.get("kind"), "value": sref["value"]}
                try:
                    info = self.herdr("pane", "process-info", "--pane", p["pane_id"])
                    row["shell_pid"] = (info.get("process_info") or {}).get("shell_pid")
                    fgs = (info.get("process_info") or {}).get("foreground_processes") or []
                    fg = next((f for f in fgs if agent_index(f.get("argv"), ag) is not None), None)
                    if fg:
                        row["argv"] = fg.get("argv") or None
                        row["pid"] = fg.get("pid")
                except RuntimeError as e:
                    log(f"process-info {p['pane_id']}: {e}")
                if row["pid"]:
                    try:
                        raw = Path(f"/proc/{row['pid']}/cmdline").read_bytes()
                        row["proc_cmdline"] = [a.decode(errors="replace") for a in raw.split(b"\0") if a]
                    except OSError:
                        row["proc_cmdline"] = None
                resumable = has_transcript(ag, row["session"], self.home)
                row["action"] = "resume" if resumable else "fresh"
                row["relaunch"] = relaunch_argv(ag, row["session"], row["argv"], resumable)
            rows.append(row)
        if no_session:
            raise Abort("agent pane(s) with no session reference: " + ", ".join(no_session)
                        + " (an agent stuck on a dialog? answer it and re-run)")
        orphans = self.find_orphans(rows)
        for o in orphans:
            log(f"warning: pid {o['pid']} in {self.v['OLD_WEB_UNIT']} is not a pane process and dies at step 4: "
                + shlex.join(o["argv"]))
        self.manifest = {"run_id": self.run_id, "started_at": self.result["started_at"], "coordinator": coordinator,
                         "tab_count": tabs, "panes": rows, "orphans": orphans}
        self.result["orphans"] = orphans
        if write:
            self.run_dir.mkdir(parents=True)
            self.save_json("manifest.json", self.manifest)
            shutil.copyfile(sj, self.run_dir / "session.json")
            snaps = sorted(glob.glob(str(self.herdr_home / "session-snapshots" / "*.json")))
            if snaps:
                shutil.copyfile(snaps[-1], self.run_dir / "session-snapshot.json")
            else:
                log("warning: no session snapshot found to copy")
        return self.manifest

    def agent_rows(self) -> list[dict]:
        """Agent rows in action order: everyone else first, the coordinator last."""
        rows = [r for r in self.manifest["panes"] if r["agent"]]
        return [r for r in rows if not r["coordinator"]] + [r for r in rows if r["coordinator"]]

    def plan(self) -> list[str]:
        v = self.v
        out = [f"herdr pane send-keys {r['pane_id']} ctrl+c ctrl+c   # quit {r['agent']}" for r in self.agent_rows()]
        out += [f"systemctl stop {v['OLD_WEB_UNIT']}   # also ends the old in-cgroup herdr server",
                f"move {self.backend}/{{app,static,requirements.txt}} to <run>/old-tree; copy {self.stage}/backend into place",
                f"render {v['WEB_UNIT']}; systemctl disable {v['OLD_WEB_UNIT']}; move its unit file to <run>/old-units",
                f"systemctl enable {v['WEB_UNIT']}; systemctl start {v['WEB_UNIT']}   # herdr.service not installed yet",
                "attach to the web terminal socket: expect close 4503 and no herdr server",
                f"render {v['HERDR_UNIT']}; systemctl enable {v['HERDR_UNIT']}; systemctl start {v['HERDR_UNIT']}",
                f"install deploy/herdr-update.sh at {v['UPDATE_SCRIPT']} (old one to <run>/old-update-script)"]
        out += [f"herdr pane run {r['pane_id']} {shlex.join(r['relaunch'])}" for r in self.agent_rows() if r["relaunch"]]
        out += [f"herdr agent wait {r['pane_id']} --until idle; send-text 'resume'; send-keys Enter"
                for r in self.agent_rows() if r["action"] == "resume"]
        out += [f"systemctl restart {v['WEB_UNIT']}   # once; the herdr server must keep its pid",
                f"notify: {v.get('NOTIFY_CMD') or '(none configured)'}",
                "on a failure in the deploy steps: automatic rollback; later: cutover.sh --rollback"]
        return out

    # ---- step 2: idle gate -----------------------------------------------------------------
    def idle_gate(self) -> None:
        if self.args.force:
            log("--force: idle gate skipped")
            self.step(2, True, forced=True)
            return
        ids = {r["pane_id"] for r in self.manifest["panes"] if r["agent"]}
        end = time.time() + self.t_idle
        while True:
            try:
                cur = [a for a in self.agents() if a["pane_id"] in ids]
            except RuntimeError as e:
                raise Abort(f"idle gate: cannot list agents: {e}")
            blocked = [a for a in cur if a.get("agent_status") == "blocked"]
            if blocked:
                raise Abort("agent pane(s) blocked: " + ", ".join(f"{a['pane_id']} (blocked)" for a in blocked)
                            + " (answer them, or --force)")
            working = [a for a in cur if a.get("agent_status") == "working"]
            if not working:
                self.step(2, True)
                return
            if time.time() >= end:
                raise Abort("agent pane(s) still working after the idle wait: "
                            + ", ".join(f"{a['pane_id']} (working)" for a in working))
            time.sleep(0.25)

    # ---- step 3: quit ----------------------------------------------------------------------
    def quit_agent(self, row: dict) -> str:
        pane = row["pane_id"]
        self.herdr("pane", "send-keys", pane, "ctrl+c", "ctrl+c")        # ONE call: two ^C in a row
        if self.wait_for(lambda: not self.has_agent(pane), self.t_quit, 0.1):
            return "clean"
        pid, want = row.get("pid"), row.get("proc_cmdline")
        if pid and want:
            try:
                cur = [a.decode(errors="replace") for a in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if a]
            except OSError:
                cur = None
            if cur == want:
                log(f"{pane}: did not quit, SIGTERM to pid {pid}")
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except OSError:
                    pass
                if self.wait_for(lambda: not self.has_agent(pane), self.t_quit, 0.1):
                    return "sigterm"
            else:
                log(f"{pane}: pid {pid} no longer matches the manifest cmdline; not signalling it")
        raise RuntimeError(f"{pane}: {row['agent']} did not quit")

    def step3_quit(self) -> None:
        try:
            for row in self.agent_rows():
                self.quit_how[row["pane_id"]] = self.quit_agent(row)
        except RuntimeError as e:
            self.step(3, False, error=str(e), quit=self.quit_how)
            self.recover_quit_agents()
            raise Abort(f"step 3: {e}; agents already quit were relaunched")
        shutil.copyfile(self.herdr_home / "session.json", self.run_dir / "session.json.after-quit")
        self.step(3, True, quit=self.quit_how)

    def recover_quit_agents(self) -> None:
        """A step-3 failure leaves agents already quit on a still-old server: bring them back."""
        rows = [r for r in self.agent_rows() if r["pane_id"] in self.quit_how]
        try:
            self.relaunch(rows, wait_native=False)
            self.resume(rows)
        except Exception as e:                                       # best effort, recorded
            self.problem(f"recovery after a failed quit: {e}")

    # ---- step 4: stop web ------------------------------------------------------------------
    def session_fits(self, path: Path) -> bool:
        try:
            return tab_count(path) == self.manifest["tab_count"]
        except (OSError, ValueError):
            return False

    def step4_stop_web(self) -> None:
        self.mark(old_web_stop_started=True)
        self.systemctl("stop", self.v["OLD_WEB_UNIT"])
        if not self.wait_for(lambda: not self.server_pids() and self.socket_refuses(), self.t_socket):
            raise StepFailed("a herdr server is still running (or the socket still answers) after stopping the web unit")
        restored = None
        live = self.herdr_home / "session.json"
        if not self.session_fits(live):
            existed = live.exists()
            if existed:
                shutil.copyfile(live, self.run_dir / "session.json.at-stop")
            for name in ("session.json.after-quit", "session.json"):
                if self.session_fits(self.run_dir / name):
                    self.mark(session_restored=True)
                    shutil.copyfile(self.run_dir / name, live)
                    if not existed:                       # never leave a root-owned file in the service user's home
                        os.chown(live, os.stat(self.herdr_home).st_uid, os.stat(self.herdr_home).st_gid) \
                            if os.geteuid() == 0 else None
                    restored = name
                    break
            if not self.session_fits(live):
                raise StepFailed("session.json is broken after the stop and no manifest copy fits the tab count")
        self.step(4, True, session_restored_from=restored)

    # ---- step 5: deploy --------------------------------------------------------------------
    def step5_deploy(self) -> None:
        v = self.v
        old_tree, st = self.run_dir / "old-tree", self.stage / "backend"
        old_tree.mkdir(exist_ok=True)
        ref = os.stat(self.backend)
        parts = [p for p in ("app", "static", "requirements.txt")
                 if (self.backend / p).exists() or (self.backend / p).is_symlink()]
        self.mark(tree_replaced=True, old_parts=parts)
        for p in parts:
            os.rename(self.backend / p, old_tree / p)           # same filesystem, checked in preflight
        for p in ("app", "static", "requirements.txt"):
            src, dst = st / p, self.backend / p
            if src.is_dir():
                shutil.copytree(src, dst, symlinks=True)
            else:
                shutil.copy2(src, dst)
            chown_tree(dst, ref.st_uid, ref.st_gid)
        rev = self.app / "REVISION"
        self.mark(revision_written=True)
        if rev.exists():
            os.rename(rev, old_tree / "REVISION")
        shutil.copyfile(self.stage / "REVISION", rev)
        chown_tree(rev, ref.st_uid, ref.st_gid)
        # the web unit only: herdr.service is not installed yet because the web unit's Wants= would start it
        web_text = self.render("web")
        self.mark(web_unit_written=True)
        atomic_write(self.units / v["WEB_UNIT"], web_text.encode(), 0o644)
        # retire the legacy web unit in the same step that starts the new one
        self.mark(old_unit_disable_started=True)
        self.systemctl("disable", v["OLD_WEB_UNIT"])
        (self.run_dir / "old-units").mkdir(exist_ok=True)
        self.mark(old_unit_moved=True)
        move_file(self.units / v["OLD_WEB_UNIT"], self.run_dir / "old-units" / v["OLD_WEB_UNIT"])
        self.systemctl("daemon-reload")
        self.systemctl("enable", v["WEB_UNIT"])
        self.systemctl("start", v["WEB_UNIT"])
        if not self.wait_for(self.web_up, self.t_socket):
            raise StepFailed("the new web unit never accepted a connection")
        self.result["guard"] = self.prove_guard()
        self.step(5, True, revision=(self.stage / "REVISION").read_text().strip())

    def prove_guard(self) -> dict:
        conn, port, bind = self.web_addr()
        hosts: list[str] = []
        for h in (None if bind in ("0.0.0.0", "", "::") else bind, "127.0.0.1", "localhost", self.v.get("HOST_NAME")):
            if h and h not in hosts:
                hosts.append(h)
        code, tried = None, []
        for h in hosts:
            status, c = ws_attach_close_code(conn, port, f"{h}:{port}")
            tried.append((h, status, c))
            if status == 101:
                code = c
                break
        appeared = bool(self.server_pids()) or not self.socket_refuses()
        if code != 4503 or appeared:
            raise StepFailed(f"the guard did not hold: close code {code}, server appeared {appeared}; tried {tried}")
        return {"code": code, "server_appeared": appeared}

    # ---- step 6: herdr.service -------------------------------------------------------------
    def step6_herdr(self) -> None:
        v = self.v
        text = self.render("herdr")
        self.mark(herdr_unit_written=True)
        atomic_write(self.units / v["HERDR_UNIT"], text.encode(), 0o644)
        self.systemctl("daemon-reload")
        self.systemctl("enable", v["HERDR_UNIT"])
        self.systemctl("start", v["HERDR_UNIT"])
        if not self.wait_for(lambda: len(self.server_pids()) >= 1 and not self.socket_refuses(), self.t_socket):
            raise StepFailed("the herdr server did not come up under herdr.service")
        servers = self.server_pids()
        if len(servers) != 1:
            raise StepFailed(f"expected exactly one herdr server, found {servers}")
        p = subprocess.run(self.privileged("systemctl", "show", "-p", "MainPID", "--value", v["HERDR_UNIT"]),
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        if p.stdout.strip() != str(servers[0]):
            raise StepFailed(f"MainPID of {v['HERDR_UNIT']} is {p.stdout.strip()!r}, the server is {servers[0]}")
        want = len(self.manifest["panes"])
        if not self.wait_for(lambda: len(self.safe_panes()) == want, self.t_socket):
            raise StepFailed(f"pane count after the restore is {len(self.safe_panes())}, the manifest has {want}")
        # the nightly script: the old one is kept in the run dir
        had = self.update_script.exists()
        old_mode = (os.stat(self.update_script).st_mode & 0o7777) if had else None
        self.mark(update_script_replaced=True, old_update_script=had, old_update_mode=old_mode)
        if had:
            shutil.copy2(self.update_script, self.run_dir / "old-update-script")
        atomic_write(self.update_script, (HERE / "herdr-update.sh").read_bytes(), 0o777,
                     like=None, owner=self.service_ids())
        self.step(6, True, server_pid=servers[0], pane_count=want)

    def service_ids(self) -> tuple[int, int]:
        import grp
        try:
            gid = grp.getgrnam(self.v["SERVICE_GROUP"]).gr_gid
        except KeyError:
            gid = pwd.getpwnam(self.v["SERVICE_USER"]).pw_gid
        return int(self.v["SERVICE_UID"]), gid

    def finalize_files(self) -> None:
        """Files this run created for the service user: owned by it, mode 777. Never touches the old tree or
        the old unit (restored byte for byte), nor UNIT_DIR files (root-owned, 644)."""
        try:
            uid, gid = self.service_ids()
            targets = [self.run_dir, self.state / "latest"]
            if self.run_dir.is_dir():
                targets += [self.run_dir / n for n in os.listdir(self.run_dir)]
            for t in targets:
                try:
                    if os.geteuid() == 0:
                        os.lchown(t, uid, gid)
                    if not t.is_symlink():
                        if t.is_dir() or t.is_file():
                            os.chmod(t, 0o777)
                except OSError:
                    pass
        except Exception as e:                                    # cosmetic: never fail the run for it
            log(f"could not fix ownership of the run files: {e}")

    def safe_panes(self) -> list[dict]:
        try:
            return self.panes()
        except RuntimeError:
            return []

    # ---- step 7: relaunch ------------------------------------------------------------------
    def agent_result(self, pane_id: str) -> dict:
        for a in self.result["agents"]:
            if a["pane_id"] == pane_id:
                return a
        a = {"pane_id": pane_id, "relaunched": False, "native": False, "session_match": False,
             "resume_sent": False, "skipped": None}
        self.result["agents"].append(a)
        return a

    def session_is(self, pane_id: str, want: str | None) -> tuple[bool, str | None]:
        """Poll up to RELAUNCH_WAIT_S for the pane's agent session to equal `want` (herdr reports it late)."""
        seen: list = [None]

        def ok() -> bool:
            try:
                p = self.pane(pane_id)
            except RuntimeError:
                return False
            seen[0] = ((p or {}).get("agent_session") or {}).get("value")
            return want is None or seen[0] == want
        return self.wait_for(ok, self.t_relaunch, 0.2), seen[0]

    def relaunch(self, rows: list[dict], wait_native: bool = True) -> None:
        for row in rows:
            pane, res = row["pane_id"], self.agent_result(row["pane_id"])
            want = (row["session"] or {}).get("value")
            try:
                if wait_native and self.quit_how.get(pane) == "sigterm":   # kept its session: herdr may resume it
                    self.wait_for(lambda: self.has_agent(pane), self.t_relaunch, 0.2)
                cur = self.pane(pane)
                if cur is None:
                    res["skipped"] = "pane no longer exists"
                    self.problem(f"{pane}: pane is gone after the restart")
                    continue
                if cur.get("agent"):
                    res["native"] = True
                    res["session_match"], val = self.session_is(pane, want)
                    if not res["session_match"]:
                        self.problem(f"{pane}: resumed natively with session {val!r}, the manifest has {want!r}")
                    continue
                if not self.wait_for(lambda: not self.has_agent(pane), self.t_relaunch, 0.2):
                    raise RuntimeError("pane did not return to a shell")
                self.herdr("pane", "run", pane, shlex.join(row["relaunch"]))
                res["relaunched"] = True
                if not self.wait_for(lambda: self.has_agent(pane), self.t_relaunch, 0.2):
                    res["session_match"] = False
                    self.problem(f"{pane}: {row['agent']} did not appear after the relaunch")
                    continue
                if row["action"] == "resume":
                    res["session_match"], val = self.session_is(pane, want)
                    if not res["session_match"]:
                        self.problem(f"{pane}: relaunched with session {val!r}, the manifest has {want!r}")
                else:
                    res["session_match"] = True                    # a fresh agent has nothing to match
            except RuntimeError as e:
                res["skipped"] = str(e)
                self.problem(f"{pane}: relaunch failed: {e}")

    # ---- step 8: resume --------------------------------------------------------------------
    def resume(self, rows: list[dict]) -> None:
        for row in rows:
            pane, res = row["pane_id"], self.agent_result(row["pane_id"])
            if res["skipped"]:
                continue
            if row["action"] != "resume":
                res["skipped"] = "fresh: no conversation to resume"
                continue
            if not res["session_match"]:
                res["skipped"] = "session mismatch: not resuming a different conversation"
                continue
            try:
                r = self.herdr("agent", "wait", pane, "--until", "idle", "--until", "blocked",
                               "--timeout", str(int(self.t_idle / 2 * 1000)), timeout=self.t_idle / 2 + 15)
                if (r.get("agent") or {}).get("agent_status") == "blocked":
                    res["skipped"] = "blocked on a dialog"
                    self.problem(f"{pane}: blocked after the relaunch; `resume` not sent")
                    continue
                self.herdr("pane", "send-text", pane, "resume")
                self.herdr("pane", "send-keys", pane, "Enter")
                res["resume_sent"] = True
            except RuntimeError as e:
                res["skipped"] = f"agent never went idle: {e}"
                self.problem(f"{pane}: {e}")

    # ---- step 9: verify + report -----------------------------------------------------------
    def step9_verify(self) -> None:
        before = self.server_pids()
        info = {"server_pid_before": before[0] if len(before) == 1 else None, "server_pid_after": None}
        try:
            self.systemctl("restart", self.v["WEB_UNIT"])
            self.wait_for(self.web_up, self.t_socket)
            after = self.server_pids()
            info["server_pid_after"] = after[0] if len(after) == 1 else None
            if info["server_pid_before"] is None or info["server_pid_before"] != info["server_pid_after"]:
                self.problem(f"the herdr server pid changed across the web restart: {before} -> {after}")
        except StepFailed as e:
            self.problem(str(e))
        self.result["web_restart"] = info
        self.step(9, not self.problems)

    def notify(self, text: str) -> None:
        cmd = self.v.get("NOTIFY_CMD")
        if not cmd:
            return
        try:
            subprocess.run([*shlex.split(cmd), text], capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            log(f"notify failed: {e}")

    def finish(self, outcome: str, **extra) -> int:
        self.result["outcome"] = outcome
        self.result.update(extra)
        self.save_json("result.json", self.result)
        host = self.v.get("HOST_NAME") or "host"
        self.notify(f"herdr cutover on {host}: {outcome}. Result file: {self.state / 'latest' / 'result.json'}")
        log(f"outcome: {outcome}; result: {self.run_dir / 'result.json'}")
        return EXIT[outcome]

    # ---- rollback --------------------------------------------------------------------------
    def rollback(self, run_dir: Path, progress: dict) -> bool:
        """Undo exactly what `progress` says was started. Best effort, every error recorded. Never deletes."""
        v = self.v
        errors: list[str] = []

        def attempt(label, fn):
            try:
                fn()
            except Exception as e:                       # keep undoing the rest
                errors.append(f"{label}: {e}")
                log(f"rollback: {label} FAILED: {e}")
        herdr_unit, web_unit = self.units / v["HERDR_UNIT"], self.units / v["WEB_UNIT"]
        if progress.get("herdr_unit_written") and herdr_unit.exists():
            attempt("stop herdr.service", lambda: self.systemctl("stop", v["HERDR_UNIT"], check=False))
            attempt("disable herdr.service", lambda: self.systemctl("disable", v["HERDR_UNIT"], check=False))
        if progress.get("web_unit_written") and web_unit.exists():
            attempt("stop the web unit", lambda: self.systemctl("stop", v["WEB_UNIT"], check=False))
            attempt("disable the web unit", lambda: self.systemctl("disable", v["WEB_UNIT"], check=False))
        if not self.wait_for(lambda: not self.server_pids() and self.socket_refuses(), self.t_socket):
            errors.append("a herdr server is still running after the new units were stopped")
        new_units = run_dir / "new-units"
        for flag, p in (("herdr_unit_written", herdr_unit), ("web_unit_written", web_unit)):
            if progress.get(flag) and p.exists():
                new_units.mkdir(exist_ok=True)
                attempt(f"move {p.name} aside", lambda p=p: move_file(p, new_units / p.name))
        if progress.get("tree_replaced"):
            old_tree, new_tree = run_dir / "old-tree", run_dir / "new-tree"
            old_parts = progress.get("old_parts", [])
            for part in ("app", "static", "requirements.txt"):
                def restore(part=part):
                    cur, old = self.backend / part, old_tree / part
                    had_old = part in old_parts and (old.exists() or old.is_symlink())
                    if (cur.exists() or cur.is_symlink()) and (had_old or part not in old_parts):
                        new_tree.mkdir(exist_ok=True)
                        os.rename(cur, new_tree / part)
                    if had_old:
                        os.rename(old, cur)
                attempt(f"restore {part}", restore)
        if progress.get("revision_written"):
            def restore_rev():
                cur, old = self.app / "REVISION", run_dir / "old-tree" / "REVISION"
                if cur.exists():
                    (run_dir / "new-tree").mkdir(exist_ok=True)
                    os.rename(cur, run_dir / "new-tree" / "REVISION")
                if old.exists():
                    os.rename(old, cur)
            attempt("restore REVISION", restore_rev)
        if progress.get("old_unit_moved"):
            def restore_unit():
                src, dst = run_dir / "old-units" / v["OLD_WEB_UNIT"], self.units / v["OLD_WEB_UNIT"]
                if src.exists() and not dst.exists():
                    move_file(src, dst)
            attempt("restore the legacy unit file", restore_unit)
        if progress.get("update_script_replaced") and progress.get("old_update_script"):
            def restore_update():
                src = run_dir / "old-update-script"
                like = os.stat(self.update_script) if self.update_script.exists() else None
                mode = progress.get("old_update_mode")
                atomic_write(self.update_script, src.read_bytes(), mode if mode is not None else 0o755, like)
            attempt("restore the nightly script", restore_update)
        elif progress.get("update_script_replaced"):
            errors.append("the host had no nightly script before; the installed one was left in place")
        attempt("daemon-reload", lambda: self.systemctl("daemon-reload", check=False))
        if progress.get("old_unit_disable_started"):
            attempt("enable the legacy web unit", lambda: self.systemctl("enable", v["OLD_WEB_UNIT"]))
        if progress.get("old_web_stop_started"):
            attempt("start the legacy web unit", lambda: self.systemctl("start", v["OLD_WEB_UNIT"]))
        progress["rolled_back"] = not errors
        progress["rollback_errors"] = errors
        (run_dir / "progress.json").write_text(json.dumps(progress, indent=1))
        return not errors

    def print_relaunch_hint(self, run_dir: Path) -> None:
        try:
            man = json.loads((run_dir / "manifest.json").read_text())
        except (OSError, ValueError):
            return
        log("agents quit in step 3 are not running; relaunch them by hand in each pane:")
        for r in man["panes"]:
            if r.get("relaunch"):
                log(f"  {r['pane_id']}: {shlex.join(r['relaunch'])}")

    # ---- entry points ----------------------------------------------------------------------
    def lock(self) -> None:
        fd = os.open(self.state, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise Usage("another cutover is running (STATE_DIR is locked)")
        self.lock_fd = fd

    def unlock(self) -> None:
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None

    def point_latest(self) -> None:
        link = self.state / ".latest.tmp"
        try:
            os.unlink(link)
        except OSError:
            pass
        os.symlink(self.run_id, link)
        os.replace(link, self.state / "latest")

    def dry_run(self) -> int:
        self.build_manifest(write=False)
        print(json.dumps({"manifest": self.manifest, "plan": self.plan()}, indent=1))
        return 0

    def detach(self, self_path: str, host_env: str) -> int:
        cmd = ["systemd-run", "--unit=herdr-cutover", "--collect", "--description=herdr cutover",
               self_path, "--host-env", host_env]
        if self.args.force:
            cmd.append("--force")
        if self.args.coordinator:
            cmd += ["--coordinator", self.args.coordinator]
        else:
            cmd.append("--no-coordinator")
        cmd += ["--require-unit", "--foreground"]
        log("launching detached: " + " ".join(cmd))
        p = subprocess.run(self.privileged(*cmd), capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        if p.returncode != 0:
            raise Usage(f"systemd-run failed ({p.returncode}): {p.stderr.strip()}")
        print("cutover launched as unit herdr-cutover. Follow it: sudo journalctl -u herdr-cutover -f; "
              f"result: {self.state}/latest/result.json")
        return 0

    def check_cgroup(self) -> None:
        """The cutover stops the web unit (and with it everything in its cgroup, including the coordinator's
        pane): refuse to run from inside any unit it stops, and from a detached launch outside its own unit."""
        try:
            lines = Path("/proc/self/cgroup").read_text().splitlines()
        except OSError as e:
            raise Usage(f"cannot read /proc/self/cgroup: {e}")
        path = (lines[-1].split(":", 2)[2] if lines and lines[-1].count(":") >= 2 else "")
        self.result["cgroup"] = path
        v = self.v
        if self.args.require_unit:                    # every detached launch passes --require-unit
            if path != "/system.slice/herdr-cutover.service":
                raise Usage(f"not in the herdr-cutover unit's own cgroup ({path}); refusing to run")
        elif any(u in path for u in (v["OLD_WEB_UNIT"], v["WEB_UNIT"], v["HERDR_UNIT"])):
            # a bare --foreground is what the offline fixture tests run, from whatever cgroup they are in,
            # so this can only be loud, not fatal
            log(f"WARNING: running inside a unit the cutover stops ({path}); the stop will kill this process. "
                "Launch without --foreground.")

    def foreground(self) -> int:
        self.check_cgroup()
        self.lock()
        try:
            try:
                self.build_manifest(write=True)
            except Abort as e:
                self.run_dir.mkdir(parents=True, exist_ok=True)
                self.point_latest()
                self.step(1, False, error=str(e))
                log(f"aborted: {e}")
                return self.finish("aborted", abort_reason=str(e))
            self.point_latest()
            self.mark()
            self.step(1, True)
            try:
                try:
                    self.idle_gate()
                except Abort as e:
                    self.step(2, False, error=str(e))
                    raise
                self.step3_quit()
            except Abort as e:
                log(f"aborted: {e}")
                return self.finish("aborted", abort_reason=str(e))
            def on_signal(signum, _frame):
                raise StepFailed(f"stopped by signal {signum}")
            old_handlers = {sg: signal.signal(sg, on_signal) for sg in (signal.SIGTERM, signal.SIGHUP)}
            try:
                self.step4_stop_web()
                self.step5_deploy()
                self.step6_herdr()
            except BaseException as e:                  # any failure (or stop) after the old unit stopped: roll back
                for sg in old_handlers:                 # the rollback itself must not be interrupted
                    signal.signal(sg, signal.SIG_IGN)
                log(f"FAILED: {e!r}; rolling back")
                if not isinstance(e, (StepFailed, OSError, RuntimeError)):
                    traceback.print_exc()
                ok = self.rollback(self.run_dir, self.progress)
                self.print_relaunch_hint(self.run_dir)
                return self.finish("rolled_back", abort_reason=str(e), rollback_ok=ok,
                                   rollback_errors=self.progress.get("rollback_errors", []))
            for sg, h in old_handlers.items():
                signal.signal(sg, h)
            try:
                self.relaunch(self.agent_rows())
                self.step(7, not self.problems)
                self.resume(self.agent_rows())
                self.step(8, not self.problems)
                self.step9_verify()
            except Exception as e:                      # the switch is done: record, never roll back here
                traceback.print_exc()
                self.problem(f"unexpected error in steps 7-9: {e}")
            return self.finish("problems" if self.problems else "done", problems=self.problems)
        finally:
            self.finalize_files()
            self.unlock()

    def find_rollback_run(self) -> Path:
        runs = sorted((p for p in self.state.iterdir() if p.is_dir() and not p.is_symlink()), reverse=True)
        for r in runs:
            try:
                pr = json.loads((r / "progress.json").read_text())
            except (OSError, ValueError):
                continue
            if pr.get("old_web_stop_started") and not pr.get("rolled_back"):
                return r
        raise Usage("no run in STATE_DIR has anything to roll back (none changed the host, or all are rolled back)")

    def do_rollback(self) -> int:
        run = self.find_rollback_run()
        self.lock()
        try:
            progress = json.loads((run / "progress.json").read_text())
            self.run_id, self.run_dir = run.name, run
            try:
                self.result = json.loads((run / "result.json").read_text())
            except (OSError, ValueError):
                self.result = {"run_id": run.name, "steps": []}
            log(f"rolling back run {run.name}")
            ok = self.rollback(run, progress)
            self.print_relaunch_hint(run)
            self.point_latest()
            self.finish("rolled_back", previous_outcome=self.result.get("outcome"), rollback_ok=ok,
                        rollback_errors=progress.get("rollback_errors", []))
            return 0 if ok else 5
        finally:
            self.finalize_files()
            self.unlock()


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="cutover.sh", add_help=False)
    ap.add_argument("--host-env", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--foreground", action="store_true")
    ap.add_argument("--require-unit", action="store_true")
    ap.add_argument("--no-coordinator", action="store_true")
    ap.add_argument("--coordinator", default=os.environ.get("HERDR_PANE_ID", ""))
    ap.add_argument("--self", dest="self_path", required=True)
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return 2
    os.umask(0)                                          # files for the service user are created 777
    try:
        if not args.rollback and not args.coordinator and not args.no_coordinator and os.geteuid() == 0:
            raise Usage("running as root loses HERDR_PANE_ID: pass --coordinator <pane id> "
                        "(or --no-coordinator when launching from outside herdr)")
        host_env = os.path.abspath(args.host_env)
        c = Cutover(load_host_env(host_env), args)
        if args.rollback:
            c.preflight_rollback()
            return c.do_rollback()
        c.preflight()
        if args.dry_run:
            return c.dry_run()
        if not args.foreground:
            return c.detach(args.self_path, host_env)
        return c.foreground()
    except Usage as e:
        print(f"cutover.sh: {e}", file=sys.stderr)
        return 2
    except Abort as e:                                   # a dry-run abort (a real run records it itself)
        print(f"cutover.sh: aborted: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
