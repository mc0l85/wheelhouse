#!/usr/bin/env python3
"""Helpers for deploy/cutover.sh (stdlib only; the host's system python3 runs it, as root or the
service user). SKELETON: every function raises "todo". The names and signatures below are a suggested
split, NOT a pinned interface -- the acceptance tests drive deploy/cutover.sh end to end
(backend/tests/acceptance/cutover_world.py pins the contract). cutover.sh may call this as
`python3 deploy/cutover_lib.py <subcommand> ...` for the JSON-heavy parts.
"""
from __future__ import annotations

import sys


def todo(what: str):
    raise SystemExit(f"cutover_lib: todo: {what}")


def load_host_env(path: str) -> dict:
    """KEY=VALUE lines, `#` comments, no shell expansion; refuse unknown/missing required keys."""
    todo("load_host_env")


def herdr_server_pids(herdr_bin: str, owner_uid: int) -> list[int]:
    """Processes whose argv[0] or argv[1] is herdr_bin followed by `server`, owned by owner_uid, not zombies."""
    todo("herdr_server_pids")


def socket_refuses(path: str) -> bool:
    todo("socket_refuses")


def build_manifest(panes: list, agents: list, process_info: dict, service_home: str,
                   coordinator: str | None) -> dict:
    """The manifest schema in cutover_world.py; raises/aborts if an agent pane has no session ref."""
    todo("build_manifest")


def relaunch_argv(agent: str, session: dict | None, fg_argv: list | None, resumable: bool) -> list:
    """[argv0] + kept flags + --resume <uuid> / --session <path>; fresh (no transcript) -> no session flag."""
    todo("relaunch_argv")


def session_tab_count(session_json_path: str) -> int:
    todo("session_tab_count")


def ws_attach_close_code(host: str, port: int, timeout: float) -> int | None:
    """Open /api/ws/terminal (Host + same Origin) and return the close code the server sends (None = stays open)."""
    todo("ws_attach_close_code")


def render_unit(template_path: str, values: dict) -> str:
    """Fill @APP_DIR@ @CONF_DIR@ @HERDR_BIN@ @SERVICE_USER@ @SERVICE_GROUP@ @SERVICE_UID@ @SERVICE_HOME@."""
    todo("render_unit")


if __name__ == "__main__":
    todo(" ".join(sys.argv[1:]) or "cli")
