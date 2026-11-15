#!/usr/bin/env python3
"""Fake herdr client that proves it was executed: appends one line to $FAKE_HERDR_MARKER
(when set), then behaves like herdr_echo.py's attach (banner, echo, exit after
FAKE_HERDR_SECONDS). A test that must never spawn a client asserts the marker file
does not exist."""
import os
import sys

marker = os.environ.get("FAKE_HERDR_MARKER")
if marker:
    with open(marker, "a") as f:
        f.write(f"executed pid={os.getpid()} argv={sys.argv[1:]}\n")
os.execv(sys.executable, [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "herdr_echo.py"),
                          *sys.argv[1:]])
