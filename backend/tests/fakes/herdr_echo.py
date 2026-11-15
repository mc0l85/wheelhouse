#!/usr/bin/env python3
"""Fake herdr client: echoes stdin to stdout, exits after N seconds.

  FAKE_HERDR_SECONDS  lifetime (default 5)
  FAKE_HERDR_EXIT     exit status when the lifetime ends (default 0)
Any argument other than `--session <name>` is treated as a CLI verb and answered
with an empty JSON envelope, so `herdr --session x pane list` works too.
"""
import json, os, select, sys, time

args = sys.argv[1:]
if args[:1] == ["--session"]:
    args = args[2:]
if args:
    print(json.dumps({"id": "cli:" + ".".join(args[:2]), "result": {}}))
    sys.exit(0)

deadline = time.time() + float(os.environ.get("FAKE_HERDR_SECONDS", "5"))
sys.stdout.write("fake-herdr ready\r\n"); sys.stdout.flush()
fd = sys.stdin.fileno()
while time.time() < deadline:
    r, _, _ = select.select([fd], [], [], 0.05)
    if r:
        data = os.read(fd, 4096)
        if not data:
            break
        os.write(sys.stdout.fileno(), data)
sys.exit(int(os.environ.get("FAKE_HERDR_EXIT", "0")))
