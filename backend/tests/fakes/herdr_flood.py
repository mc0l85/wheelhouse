#!/usr/bin/env python3
"""Fake herdr client that floods its terminal with a deterministic byte pattern.

  FAKE_HERDR_FLOOD_BYTES  bytes to write (default 4 MiB)
  FAKE_HERDR_SECONDS      linger after writing, then exit 0 (default 0)

The pty is put in raw mode first (no ONLCR), so the bytes reach the master
unchanged. Tests import `pattern(n)` from this module to check what arrived.
"""
import os
import sys
import time

BLOCK = 256


def block(k: int) -> bytes:
    """Block k: its index as 4 big-endian bytes, then 252 bytes (k + j) % 256."""
    return k.to_bytes(4, "big") + bytes((k + j) % 256 for j in range(4, BLOCK))


def pattern(n: int) -> bytes:
    out = bytearray()
    k = 0
    while len(out) < n:
        out += block(k)
        k += 1
    return bytes(out[:n])


def main() -> None:
    import termios
    import tty
    fd = sys.stdout.fileno()
    try:
        tty.setraw(fd, termios.TCSANOW)
    except termios.error:
        pass
    n = int(os.environ.get("FAKE_HERDR_FLOOD_BYTES", str(4 << 20)))
    data = pattern(n)
    view = memoryview(data)
    while view:
        w = os.write(fd, view[:65536])
        view = view[w:]
    time.sleep(float(os.environ.get("FAKE_HERDR_SECONDS", "0")))
    sys.exit(0)


if __name__ == "__main__":
    main()
