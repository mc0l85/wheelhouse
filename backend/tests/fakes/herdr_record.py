#!/usr/bin/env python3
"""Fake herdr client that records every byte it receives on its terminal.

  FAKE_HERDR_RECORD   file that gets every input byte, appended as it arrives
                      (<file>.winch gets "rows cols\\n" on every SIGWINCH)
  FAKE_HERDR_SECONDS  lifetime (default 10)

Raw mode first (no echo, no line buffering), then prints "fake-record ready\\r\\n".
"""
import fcntl
import os
import select
import signal
import struct
import sys
import termios
import time
import tty

rec = os.environ["FAKE_HERDR_RECORD"]
fd_in, fd_out = sys.stdin.fileno(), sys.stdout.fileno()
tty.setraw(fd_in, termios.TCSANOW)


def on_winch(*_):
    rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(fd_in, termios.TIOCGWINSZ, b"\0" * 8))
    with open(rec + ".winch", "a") as f:
        f.write(f"{rows} {cols}\n")


signal.signal(signal.SIGWINCH, on_winch)
os.write(fd_out, b"fake-record ready\r\n")
deadline = time.time() + float(os.environ.get("FAKE_HERDR_SECONDS", "10"))
while time.time() < deadline:
    try:
        r, _, _ = select.select([fd_in], [], [], 0.05)
    except InterruptedError:
        continue
    if r:
        data = os.read(fd_in, 65536)
        if not data:
            break
        with open(rec, "ab") as f:
            f.write(data)
sys.exit(0)
