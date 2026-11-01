"""herdr CLI client — the single bridge between herdr-web and the live herdr server.

Why shell out instead of speaking the Unix socket directly? The `herdr <sub>`
CLI already returns clean JSON envelopes:

    $ herdr pane list
    {"id":"cli:pane:list","result":{"panes":[...],"type":"pane_list"}}

so the CLI is a stable, versioned, JSON-RPC-ish facade over the socket. Talking
the raw socket would couple us to herdr's wire protocol (protocol v12,
"SemanticFrame" encoding) which is undocumented and changes between releases.
Shelling out is version-tolerant.

Mutating CLI verbs funnel through `_write()`, and the socket verbs (focus, zoom)
check the same enable_writes flag. Pane IDs are validated before they reach an
argv (herdr's CLI has no `--` separator, so an ID starting with "-" would parse
as an option).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import socket
import subprocess
from typing import Any

from .config import settings


class HerdrError(RuntimeError):
    def __init__(self, message: str, *, argv: list[str] | None = None, stderr: str = ""):
        super().__init__(message)
        self.argv = argv or []
        self.stderr = stderr


class WritesDisabled(HerdrError):
    """Raised when a mutating verb is attempted while enable_writes is False."""


class BadRequest(ValueError):
    """Client input herdr must never see (bad pane id, option-like key)."""


PANE_ID_RE = r"^[A-Za-z0-9][A-Za-z0-9_.]*:[A-Za-z0-9_.]+$"
_PANE_ID = re.compile(PANE_ID_RE)


def pid(pane_id: str) -> str:
    if not _PANE_ID.match(pane_id or ""):
        raise BadRequest(f"invalid pane id: {pane_id!r}")
    return pane_id


def _argv(args: list[str]) -> list[str]:
    # Every call is scoped to the configured session so herdr-web never acts on
    # the wrong session if more than one server is running.
    return [settings.herdr_bin, "--session", settings.herdr_session, *args]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    argv = _argv(args)
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=settings.cli_timeout)
    except subprocess.TimeoutExpired as e:
        raise HerdrError(f"herdr CLI timed out: {shlex.join(argv)}", argv=argv) from e
    except FileNotFoundError as e:
        raise HerdrError(f"herdr binary not found at {settings.herdr_bin}", argv=argv) from e


def _call(args: list[str]) -> Any:
    """Run a READ subcommand and return the parsed `result` payload.

    herdr emits {"id":"cli:<verb>","result":{...}} on success. Some verbs print
    plain text (e.g. `pane read --format text`); callers that expect text use
    `_call_text` instead.
    """
    p = _run(args)
    out = (p.stdout or "").strip()
    if p.returncode != 0 and not out:
        raise HerdrError(f"herdr {' '.join(args)} failed (rc={p.returncode})",
                         argv=_argv(args), stderr=(p.stderr or "").strip())
    try:
        env = json.loads(out)
    except json.JSONDecodeError:
        # Not a JSON envelope — hand back raw text under a uniform shape.
        return {"raw": out, "type": "text"}
    if isinstance(env, dict) and "result" in env:
        return env["result"]
    if isinstance(env, dict) and "error" in env:
        raise HerdrError(str(env["error"]), argv=_argv(args))
    if p.returncode != 0:
        raise HerdrError(f"herdr {' '.join(args)} failed (rc={p.returncode})",
                         argv=_argv(args), stderr=(p.stderr or "").strip())
    return env


def _call_text(args: list[str]) -> str:
    p = _run(args)
    if p.returncode != 0 and not (p.stdout or "").strip():
        raise HerdrError(f"herdr {' '.join(args)} failed (rc={p.returncode})",
                         argv=_argv(args), stderr=(p.stderr or "").strip())
    return p.stdout or ""


def _write(args: list[str]) -> Any:
    """Run a MUTATING subcommand. Fails closed when writes are disabled."""
    if not settings.enable_writes:
        raise WritesDisabled(
            "herdr-web is in read-only mode (set HERDRWEB_ENABLE_WRITES=1 to allow)")
    return _call(args)


# ---------------------------------------------------------------------------
# READ surface
# ---------------------------------------------------------------------------
def panes() -> list[dict]:
    return _call(["pane", "list"]).get("panes", [])


def agents() -> list[dict]:
    return _call(["agent", "list"]).get("agents", [])


def pane_get(pane_id: str) -> dict:
    return _call(["pane", "get", pid(pane_id)])


def tabs(workspace_id: str | None = None) -> Any:
    args = ["tab", "list"]
    if workspace_id:
        args += ["--workspace", workspace_id]
    return _call(args)


def worktrees(workspace_id: str | None = None, cwd: str | None = None) -> Any:
    args = ["worktree", "list", "--json"]
    if workspace_id:
        args += ["--workspace", workspace_id]
    elif cwd:
        args += ["--cwd", cwd]
    return _call(args)


def pane_read(pane_id: str, *, source: str = "visible", lines: int = 200,
              fmt: str = "ansi") -> str:
    """Return pane content. fmt='ansi' for the live xterm mirror, 'text' for
    clean previews/search. source in {visible, recent, recent-unwrapped}."""
    return _call_text(["pane", "read", pid(pane_id), "--source", source,
                       "--lines", str(lines), "--format", fmt])


def workspaces_from_session() -> dict:
    """The CLI has no `workspace list --json`; the authoritative workspace→tab→
    pane tree is herdr's session.json. We read it directly (read-only)."""
    try:
        with open(session_dir() + "/session.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise HerdrError(f"cannot read session.json: {e}") from e


def tree() -> dict:
    """Workspace -> tab -> pane tree in herdr's own order (matches its sidebar),
    built from the live socket lists rather than the lagging session.json.
    Each blocked pane carries `blocked_since` (epoch s) from the event hub."""
    from .events import hub   # late import: events imports this module
    wss = (_socket_request("workspace.list", {}) or {}).get("workspaces", [])
    tabs_ = (_socket_request("tab.list", {}) or {}).get("tabs", [])
    ps = (_socket_request("pane.list", {}) or {}).get("panes", [])
    hub.note_statuses(ps)
    for p in ps:
        since = hub.blocked_since.get(p.get("pane_id"))
        if since and p.get("agent_status") == "blocked":
            p["blocked_since"] = since
    out = []
    for w in wss:
        wid = w.get("workspace_id")
        tl = [{**t, "panes": [p for p in ps if p.get("tab_id") == t.get("tab_id")]}
              for t in tabs_ if t.get("workspace_id") == wid]
        out.append({**w, "tabs": tl})
    return {"workspaces": out}


def session_dir() -> str:
    sess = settings.herdr_session
    return settings.herdr_home if sess == "default" else f"{settings.herdr_home}/sessions/{sess}"


def socket_path() -> str:
    return session_dir() + "/herdr.sock"


def _socket_request(method: str, params: dict) -> Any:
    """One JSON request on herdr's control socket: verbs the CLI lacks (pane.focus
    by id), and hot reads (tree) where a CLI spawn per call is too costly."""
    path = socket_path()
    req = json.dumps({"id": f"web:{method}", "method": method, "params": params}) + "\n"
    buf = b""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(settings.cli_timeout)
            s.connect(path)
            s.sendall(req.encode())
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
    except OSError as e:
        raise HerdrError(f"herdr socket {method} failed: {e}") from e
    try:
        env = json.loads(buf.split(b"\n", 1)[0])
    except json.JSONDecodeError as e:
        raise HerdrError(f"herdr socket {method}: bad reply") from e
    if "error" in env:
        raise HerdrError(str(env["error"]))
    return env.get("result")


# ---------------------------------------------------------------------------
# WRITE surface (every verb gated by _write)
# ---------------------------------------------------------------------------
def pane_send_text(pane_id: str, text: str) -> Any:
    return _write(["pane", "send-text", pid(pane_id), text])


def pane_send_keys(pane_id: str, keys: list[str]) -> Any:
    if any(k.startswith("-") for k in keys):
        raise BadRequest("a key name cannot start with '-'")
    return _write(["pane", "send-keys", pid(pane_id), *keys])


def pane_run(pane_id: str, command: str) -> Any:
    return _write(["pane", "run", pid(pane_id), command])


def pane_split(pane_id: str, direction: str, cwd: str | None = None,
               focus: bool = False) -> Any:
    args = ["pane", "split", pid(pane_id), "--direction", direction]
    if cwd:
        args += ["--cwd", cwd]
    args += ["--focus" if focus else "--no-focus"]
    return _write(args)


def pane_focus(pane_id: str) -> Any:
    if not settings.enable_writes:
        raise WritesDisabled(
            "herdr-web is in read-only mode (set HERDRWEB_ENABLE_WRITES=1 to allow)")
    return _socket_request("pane.focus", {"pane_id": pid(pane_id)})


def pane_zoom(pane_id: str, mode: str = "toggle") -> Any:
    if not settings.enable_writes:
        raise WritesDisabled(
            "herdr-web is in read-only mode (set HERDRWEB_ENABLE_WRITES=1 to allow)")
    return _socket_request("pane.zoom", {"pane_id": pid(pane_id), "mode": mode})


def pane_close(pane_id: str) -> Any:
    return _write(["pane", "close", pid(pane_id)])


def pane_rename(pane_id: str, label: str) -> Any:
    return _write(["pane", "rename", pid(pane_id), label])


def agent_focus(target: str) -> Any:
    return _write(["agent", "focus", target])


def agent_start(name: str, argv: list[str], *, cwd: str | None = None,
                workspace: str | None = None, tab: str | None = None,
                split: str | None = None, focus: bool = False) -> Any:
    args = ["agent", "start", name]
    if cwd:
        args += ["--cwd", cwd]
    if workspace:
        args += ["--workspace", workspace]
    if tab:
        args += ["--tab", tab]
    if split:
        args += ["--split", split]
    args += ["--focus" if focus else "--no-focus", "--", *argv]
    return _write(args)


def workspace_create(cwd: str | None = None, label: str | None = None) -> Any:
    args = ["workspace", "create"]
    if cwd:
        args += ["--cwd", cwd]
    if label:
        args += ["--label", label]
    args += ["--no-focus"]
    return _write(args)


def tab_create(workspace_id: str | None = None, cwd: str | None = None,
               label: str | None = None) -> Any:
    args = ["tab", "create"]
    if workspace_id:
        args += ["--workspace", workspace_id]
    if cwd:
        args += ["--cwd", cwd]
    if label:
        args += ["--label", label]
    args += ["--no-focus"]
    return _write(args)


def worktree_create(branch: str, *, base: str | None = None,
                    workspace_id: str | None = None, cwd: str | None = None,
                    label: str | None = None) -> Any:
    args = ["worktree", "create", "--branch", branch, "--json"]
    if base:
        args += ["--base", base]
    if workspace_id:
        args += ["--workspace", workspace_id]
    elif cwd:
        args += ["--cwd", cwd]
    if label:
        args += ["--label", label]
    args += ["--no-focus"]
    return _write(args)
