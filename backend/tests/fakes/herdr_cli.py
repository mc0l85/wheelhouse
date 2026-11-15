#!/usr/bin/env python3
"""Fake `herdr` CLI + fake `herdr server` for the cutover fixture tests (P2, lane E).

Installed by tests/acceptance/cutover_world.py as a SYMLINK named `herdr` in a scratch bin dir; that
path is the host-env HERDR_BIN. Run through the shebang, /proc/<pid>/cmdline of a server is
[python3, <HERDR_BIN>, "server"] -- the cutover finds servers by "HERDR_BIN followed by server".

Every invocation is appended to $FAKE_LEDGER (JSONL): {"tool": "herdr", "argv": [...], "t": ...}.

CLI verbs (shapes copied from herdr 0.9.5; each one is a request to the fake API socket at
$HOME/.config/herdr/herdr.sock, printed as {"id": "cli:...", "result": ...}):
  pane list | agent list | pane get <id> | pane process-info --pane <id>
  pane send-keys <id> <key>... | pane send-text <id> <text> | pane run <id> <command>...
  agent wait <id> [--until S]... [--timeout MS] | status server --json
  update [--handoff] | integration status | integration install <t> | --version   (recorded only)
  server   -> runs the stateful fake server (below) on that socket
No server -> {"error": {"code": "server_not_running", ...}} on stdout, exit 1 (P0-2: no verb auto-starts one).

The fake server keeps its state in $FAKE_WORLD/state.json and rewrites it on every change, so a
restarted server "restores" the panes (layout restore). Semantics:
  - send-keys <agent pane> ctrl+c ctrl+c in ONE call: a "clean" agent quits after ~0.2 s:
    agent and agent_session cleared (P0-3), foreground back to the shell. quit: "ignore" never quits.
  - an agent whose pid died (SIGTERM fallback) loses `agent` but KEEPS agent_session (no clean quit),
    so a server start resumes it natively (resume_agents_on_restore).
  - on start, panes with agent_session but no agent come back with that agent (native resume).
  - run <pane> "<claude|pi> ..." on a shell pane: after ~0.3 s the pane shows that agent with the
    session from --resume <uuid> / --session <path> (or a fresh one), in status pane["relaunch_status"]
    ("idle" default, "blocked", or "working" -> idle after pane["working_s"] seconds).
    pane["relaunch_session"] overrides the reported session value (mismatch tests).
  - pane["idle_after_s"]: on the FIRST server start, a working agent turns idle after that many seconds.
  - each start records what it found in HERDR_HOME/session.json in state["session_at_start"].
  - every input (keys/text/run) is logged with a timestamp in state["inputs"][pane_id];
    every agent_status change in state["status_log"][pane_id] as [t, status].
  - HERDR_HOME/session.json is rewritten from the panes (one tab per distinct tab_id) and a snapshot
    goes to HERDR_HOME/session-snapshots/; on SIGTERM with world option corrupt_session_on_exit the
    server writes a broken session.json before exiting.
"""
from __future__ import annotations

import fcntl
import json
import os
import shlex
import signal
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

BACKEND = Path(os.path.realpath(__file__)).parents[2]


def ledger(entry: dict) -> None:
    path = os.environ.get("FAKE_LEDGER")
    if not path:
        return
    entry = {"t": time.time(), **entry}
    with open(path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(entry) + "\n")


def home() -> str:
    return os.environ.get("HOME", "")


def sock_path() -> str:
    return os.path.join(home(), ".config", "herdr", "herdr.sock")


def herdr_home() -> str:
    return os.path.join(home(), ".config", "herdr")


def world() -> Path:
    return Path(os.environ["FAKE_WORLD"])


# ---------------------------------------------------------------- client side

def request(method: str, params: dict | None = None, timeout: float = 30.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path())
    except OSError:
        return None, {"code": "server_not_running",
                      "message": f"no herdr server is running at {sock_path()}"}
    try:
        s.sendall((json.dumps({"id": f"cli:{method}", "method": method, "params": params or {}}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    except OSError as e:
        return None, {"code": "io", "message": str(e)}
    finally:
        s.close()
    try:
        reply = json.loads(buf.split(b"\n", 1)[0])
    except ValueError:
        return None, {"code": "bad_reply", "message": repr(buf[:200])}
    res, err = reply.get("result"), reply.get("error")
    if isinstance(res, dict) and "__error__" in res:     # the fake server's error convention
        return None, {"code": "error", "message": res["__error__"]}
    return res, err


def out(method: str, result, error) -> int:
    if error is not None:
        print(json.dumps({"id": f"cli:{method}", "error": error}))
        return 1
    print(json.dumps({"id": f"cli:{method}", "result": result}))
    return 0


def cli(argv: list[str]) -> int:
    args = list(argv)
    if args[:1] == ["--session"]:
        args = args[2:]
    if args == ["--version"]:
        print("herdr 0.9.5 (fake)")
        return 0
    if args[:1] == ["server"]:
        return serve()
    if args[:1] == ["update"]:
        print("fake update" + (" with handoff" if "--handoff" in args else ""))
        return 0
    if args[:2] == ["integration", "status"]:
        print("claude: installed\npi: installed\nopencode: outdated\nantigravity-cli: not installed")
        return 0
    if args[:2] == ["integration", "install"]:
        return 0
    if args[:3] == ["status", "server", "--json"]:
        res, err = request("server.status", timeout=2)
        if err is not None:
            print(json.dumps({"status": "not_running", "running": False, "socket": sock_path()}))
            return 0
        print(json.dumps({"status": "running", "running": True, "socket": sock_path(), **(res or {})}))
        return 0
    if args[:2] == ["pane", "list"]:
        return out("pane:list", *request("pane.list"))
    if args[:2] == ["agent", "list"]:
        return out("agent:list", *request("agent.list"))
    if args[:2] == ["pane", "get"] and len(args) >= 3:
        return out("pane:get", *request("pane.get", {"pane_id": args[2]}))
    if args[:2] == ["pane", "process-info"]:
        pid = args[args.index("--pane") + 1] if "--pane" in args else None
        return out("pane:process_info", *request("pane.process_info", {"pane_id": pid}))
    if args[:2] == ["pane", "send-keys"] and len(args) >= 4:
        return out("pane:send_keys", *request("pane.send_keys", {"pane_id": args[2], "keys": args[3:]}))
    if args[:2] == ["pane", "send-text"] and len(args) == 4:
        return out("pane:send_text", *request("pane.send_text", {"pane_id": args[2], "text": args[3]}))
    if args[:2] == ["pane", "run"] and len(args) >= 4:
        return out("pane:run", *request("pane.run", {"pane_id": args[2], "command": " ".join(args[3:])}))
    if args[:2] == ["agent", "wait"] and len(args) >= 3:
        until = [args[i + 1] for i, a in enumerate(args) if a == "--until" and i + 1 < len(args)]
        tmo = int(args[args.index("--timeout") + 1]) if "--timeout" in args else None
        return out("agent:wait", *request("agent.wait", {"target": args[2], "until": until, "timeout_ms": tmo},
                                          timeout=(tmo / 1000 + 5) if tmo else 600))
    print(json.dumps({"error": {"code": "fake_unsupported", "message": f"fake herdr: unsupported {args}"}}))
    return 2


# ---------------------------------------------------------------- server side

class State:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.path = world() / "state.json"
        self.data = json.loads(self.path.read_text())
        self.data.setdefault("inputs", {})
        self.data.setdefault("status_log", {})
        self.opts = self.data.get("options", {})

    def save(self) -> None:
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1))
            os.replace(tmp, self.path)
            self.write_session()

    def write_session(self) -> None:
        tabs: dict[str, dict] = {}
        for p in self.data["panes"]:
            tab = tabs.setdefault(p["tab_id"], {"panes": {}, "layout": {}})
            n = str(len(tab["panes"]) + 1)
            entry = {"cwd": p.get("cwd", "/")}
            if p.get("agent_session"):
                entry["agent_session"] = p["agent_session"]
            tab["panes"][n] = entry
        sess = {"version": 1, "workspaces": [{"id": "wfake", "tabs": list(tabs.values())}]}
        hh = Path(herdr_home())
        hh.mkdir(parents=True, exist_ok=True)
        (hh / "session.json").write_text(json.dumps(sess))
        snaps = hh / "session-snapshots"
        snaps.mkdir(exist_ok=True)
        (snaps / f"session-{time.time_ns():030d}-{os.getpid()}-0.json").write_text(json.dumps(sess))

    def pane(self, pid: str) -> dict | None:
        return next((p for p in self.data["panes"] if p["pane_id"] == pid), None)

    def set_status(self, p: dict, status: str) -> None:
        p["agent_status"] = status
        self.data["status_log"].setdefault(p["pane_id"], []).append([time.time(), status])

    def log_input(self, pane_id: str, kind: str, data) -> None:
        self.data["inputs"].setdefault(pane_id, []).append({"t": time.time(), "kind": kind, "data": data})

    def reap_dead(self) -> None:
        changed = False
        for p in self.data["panes"]:
            pid = p.get("pid")
            if p.get("agent") and pid and not _alive(pid):
                p["agent"] = None                       # no clean quit: agent_session is kept
                p["fg_argv"] = None
                self.set_status(p, "idle")
                changed = True
        if changed:
            self.save()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:                                                 # a zombie child counts as dead
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(") ", 1)[1][0] != "Z"
    except OSError:
        return False


def api_pane(p: dict) -> dict:
    d = {k: p[k] for k in ("pane_id", "tab_id", "workspace_id", "cwd") if k in p}
    d["foreground_cwd"] = p.get("cwd")
    if p.get("agent"):
        d["agent"] = p["agent"]
        d["agent_status"] = p.get("agent_status", "idle")
        if p.get("agent_session"):
            d["agent_session"] = p["agent_session"]
    else:
        d["agent_status"] = "unknown"
    return d


def serve() -> int:
    st = State()
    st.data["server_starts"] = st.data.get("server_starts", 0) + 1
    sj = Path(herdr_home(), "session.json")             # what this start found on disk (restore input)
    try:
        sess = json.loads(sj.read_text())
        found = {"parses": True, "tabs": sum(len(w.get("tabs", [])) for w in sess.get("workspaces", []))}
    except (OSError, ValueError):
        found = {"parses": False, "tabs": None}
    st.data.setdefault("session_at_start", []).append({"t": time.time(), **found})
    for p in st.data["panes"]:                           # native resume of panes that kept a session
        if not p.get("agent") and p.get("agent_session"):
            p["agent"] = p["agent_session"]["agent"]
            p["fg_argv"] = [p["agent"]]
            p["pid"] = None                              # a new process; the old pid is gone
            st.set_status(p, "idle")
    st.save()
    from tests.fakes.fake_herdr_api import FakeHerdrApi   # noqa: E402  (sys.path set in __main__)

    api = FakeHerdrApi(sock_path())

    def h(fn):
        def wrapped(params):
            with st.lock:
                st.reap_dead()
                return fn(params)
        return wrapped

    def pane_list(_):
        return {"panes": [api_pane(p) for p in st.data["panes"]]}

    def agent_list(_):
        return {"agents": [api_pane(p) for p in st.data["panes"] if p.get("agent")]}

    def pane_get(params):
        p = st.pane(params.get("pane_id"))
        if not p:
            raise KeyError("no such pane")
        return {"pane": api_pane(p)}

    def process_info(params):
        p = st.pane(params.get("pane_id"))
        if not p:
            raise KeyError("no such pane")
        fg = []
        if p.get("agent") and p.get("fg_argv"):
            fg = [{"argv": p["fg_argv"], "cmdline": " ".join(p["fg_argv"]), "pid": p.get("pid"),
                   "name": os.path.basename(p["fg_argv"][0]), "cwd": p.get("cwd")}]
        return {"process_info": {"pane_id": p["pane_id"], "shell_pid": p.get("shell_pid"),
                                 "foreground_processes": fg}, "type": "pane_process_info"}

    def quit_later(p):
        time.sleep(0.2)
        with st.lock:
            p["agent"] = None
            p["agent_session"] = None
            p["fg_argv"] = None
            st.set_status(p, "idle")
            st.save()

    def send_keys(params):
        p = st.pane(params.get("pane_id"))
        if not p:
            raise KeyError("no such pane")
        keys = params.get("keys") or []
        st.log_input(p["pane_id"], "keys", keys)
        if keys == ["ctrl+c", "ctrl+c"] and p.get("agent") and p.get("quit", "clean") == "clean":
            threading.Thread(target=quit_later, args=(p,), daemon=True).start()
        st.save()
        return {"type": "ok"}

    def send_text(params):
        p = st.pane(params.get("pane_id"))
        if not p:
            raise KeyError("no such pane")
        st.log_input(p["pane_id"], "text", params.get("text"))
        st.save()
        return {"type": "ok"}

    def start_agent(p, argv):
        time.sleep(0.3)
        name = os.path.basename(argv[0])
        value = None
        for flag in ("--resume", "--session"):
            if flag in argv and argv.index(flag) + 1 < len(argv):
                value = argv[argv.index(flag) + 1]
        kind = "id" if name == "claude" else "path"
        if value is None:
            value = str(uuid.uuid4()) if name == "claude" else f"/fresh/{uuid.uuid4()}.jsonl"
        value = p.get("relaunch_session", value)
        with st.lock:
            p["agent"] = name
            p["agent_session"] = {"agent": name, "kind": kind, "source": f"herdr:{name}", "value": value}
            p["fg_argv"] = argv
            p["pid"] = None
            want = p.get("relaunch_status", "idle")
            st.set_status(p, "working" if want == "working" else want)
            st.save()
        if want == "working":
            time.sleep(float(p.get("working_s", 1.5)))
            with st.lock:
                st.set_status(p, "idle")
                st.save()

    def run(params):
        p = st.pane(params.get("pane_id"))
        if not p:
            raise KeyError("no such pane")
        cmd = params.get("command") or ""
        st.log_input(p["pane_id"], "run", cmd)
        try:
            argv = shlex.split(cmd)
        except ValueError:
            argv = []
        if argv and not p.get("agent") and os.path.basename(argv[0]) in ("claude", "pi"):
            threading.Thread(target=start_agent, args=(p, argv), daemon=True).start()
        st.save()
        return {"type": "ok"}

    def agent_wait(params):
        until = params.get("until") or ["idle", "done", "blocked"]
        tmo = params.get("timeout_ms")
        end = time.time() + (tmo / 1000 if tmo else 600)
        while time.time() < end:
            with st.lock:
                st.reap_dead()
                p = st.pane(params.get("target"))
                if p is None:
                    raise KeyError("no such pane")
                if p.get("agent") and p.get("agent_status") in until:
                    return {"agent": api_pane(p)}
            time.sleep(0.05)
        raise TimeoutError("timed out waiting for agent state")

    def status(_):
        return {"version": "0.9.5", "pid": os.getpid()}

    def guard(fn):                                       # errors travel as {"__error__": msg}
        def g(params):
            try:
                # agent.wait polls and takes the lock per poll: holding it would starve the status threads
                return fn(params) if fn is agent_wait else h(fn)(params)
            except (KeyError, TimeoutError) as e:
                return {"__error__": str(e)}
        return g

    table = {"pane.list": pane_list, "agent.list": agent_list, "pane.get": pane_get,
             "pane.process_info": process_info, "pane.send_keys": send_keys, "pane.send_text": send_text,
             "pane.run": run, "agent.wait": agent_wait, "server.status": status}
    for method, fn in table.items():
        api.on(method, handler=guard(fn))
    api.start()

    def idle_later(p, delay):
        time.sleep(delay)
        with st.lock:
            if p.get("agent"):
                st.set_status(p, "idle")
                st.save()
    if st.data["server_starts"] == 1:
        for p in st.data["panes"]:
            if p.get("idle_after_s") is not None:
                threading.Thread(target=idle_later, args=(p, float(p["idle_after_s"])), daemon=True).start()

    def on_term(*_):
        if st.opts.get("corrupt_session_on_exit"):
            Path(herdr_home(), "session.json").write_text("{ broken by the fake server's exit")
        api.stop()
        os._exit(0)
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    sys.path.insert(0, str(BACKEND))
    ledger({"tool": "herdr", "argv": sys.argv[1:]})
    sys.exit(cli(sys.argv[1:]))
