"""Unit tests for the relaunch argv the cutover builds (deploy/cutover_lib.py), offline."""
from __future__ import annotations

import importlib.util
from pathlib import Path

LIB = Path(__file__).resolve().parents[2] / "deploy" / "cutover_lib.py"
spec = importlib.util.spec_from_file_location("cutover_lib", LIB)
lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lib)

SID = {"kind": "id", "value": "U-1"}


def test_claude_binary_with_flags_and_stale_resume():
    got = lib.relaunch_argv("claude", SID, ["/x/bin/claude", "--dangerously-skip-permissions", "--resume", "X"], True)
    assert got == ["/x/bin/claude", "--dangerously-skip-permissions", "--resume", "U-1"]


def test_node_run_agent_drops_the_interpreter_and_script_is_the_command():
    got = lib.relaunch_argv("claude", SID, ["/usr/bin/node", "/opt/lib/claude", "--model", "opus"], True)
    assert got == ["/opt/lib/claude", "--model", "opus", "--resume", "U-1"]


def test_continue_is_replaced():
    assert lib.relaunch_argv("claude", SID, ["claude", "-c"], True) == ["claude", "--resume", "U-1"]
    assert lib.relaunch_argv("claude", SID, ["claude", "--continue"], False) == ["claude"]


def test_positional_prompt_is_dropped():
    got = lib.relaunch_argv("claude", SID, ["/x/claude", "--dangerously-skip-permissions", "some prompt"], True)
    assert got == ["/x/claude", "--dangerously-skip-permissions", "--resume", "U-1"]
    assert lib.relaunch_argv("claude", SID, ["/x/claude", "some prompt"], False) == ["/x/claude"]


def test_session_flag_without_value_does_not_eat_the_next_flag():
    got = lib.relaunch_argv("claude", SID, ["claude", "-r", "--model", "opus"], True)
    assert got == ["claude", "--model", "opus", "--resume", "U-1"]
    assert lib.relaunch_argv("claude", SID, ["claude", "--resume"], True) == ["claude", "--resume", "U-1"]


def test_pi_session_path_and_node_form():
    pi = {"kind": "path", "value": "/s/a.jsonl"}
    assert lib.relaunch_argv("pi", pi, ["/x/pi/bin/pi", "--session", "/old"], True) == ["/x/pi/bin/pi", "--session", "/s/a.jsonl"]
    assert lib.relaunch_argv("pi", pi, ["node", "/x/pi", "--no-session"], True) == ["/x/pi", "--no-session", "--session", "/s/a.jsonl"]


def test_no_agent_element_falls_back_to_the_bare_name_without_guessing_flags():
    assert lib.relaunch_argv("claude", SID, ["/usr/bin/python3", "x.py"], False) == ["claude"]
    assert lib.relaunch_argv("claude", SID, None, True) == ["claude", "--resume", "U-1"]
