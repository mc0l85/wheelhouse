#!/usr/bin/env python3
"""Fake herdr-web for the cutover fixture tests, started by systemd_fake.py as a unit's process.

  GET /api/health            -> 200 {"ok": true}
  GET /api/ws/terminal ...   -> WebSocket handshake (101), then:
      herdr socket answers   -> stays open (a terminal) until the client closes
      herdr socket is down   -> NEW backend (APP_DIR/backend/app/MARKER == the staged marker):
                                close 4503 "herdr server not running" (the A-3 guard), spawns nothing
                                OLD backend (anything else): auto-starts `HERDR_BIN server` inside this
                                unit (what herdr 0.9.5's client attach does) and stays open
Env: FAKE_WORLD, FAKE_WEB_HOST, FAKE_WEB_PORT, FAKE_WEB_UNIT, HOME (the service home).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import threading
from pathlib import Path

W = Path(os.environ["FAKE_WORLD"])
CFG = json.loads((W / "config.json").read_text())
UNIT = os.environ["FAKE_WEB_UNIT"]
SOCK = os.path.join(os.environ["HOME"], ".config", "herdr", "herdr.sock")
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def herdr_up() -> bool:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(SOCK)
        s.sendall(b'{"id":"web:probe","method":"server.status","params":{}}\n')
        return bool(s.recv(1))
    except OSError:
        return False
    finally:
        s.close()


def new_backend() -> bool:
    m = Path(CFG["APP_DIR"], "backend", "app", "MARKER")
    return m.exists() and m.read_text() == CFG["STAGED_MARKER"]


def close_frame(code: int, reason: str) -> bytes:
    payload = struct.pack("!H", code) + reason.encode()
    return bytes([0x88, len(payload)]) + payload


def handle(c: socket.socket) -> None:
    try:
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = c.recv(4096)
            if not chunk:
                return
            buf += chunk
        head = buf.split(b"\r\n\r\n", 1)[0].decode(errors="replace")
        lines = head.split("\r\n")
        path = lines[0].split(" ")[1] if len(lines[0].split(" ")) > 1 else "/"
        hdrs = {ln.split(":", 1)[0].strip().lower(): ln.split(":", 1)[1].strip() for ln in lines[1:] if ":" in ln}
        if not path.startswith("/api/ws/terminal"):
            body = b'{"ok": true}'
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
            return
        key = hdrs.get("sec-websocket-key", "")
        acc = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        c.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Accept: {acc}\r\n\r\n").encode())
        with open(W / "web_attaches.jsonl", "a") as f:
            f.write(json.dumps({"unit": UNIT, "herdr_up": herdr_up(), "new_backend": new_backend()}) + "\n")
        if not herdr_up():
            if new_backend():
                c.sendall(close_frame(4503, "herdr server not running"))
                return
            p = subprocess.Popen([CFG["HERDR_BIN"], "server"], env=dict(os.environ, HOME=os.environ["HOME"]),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            with open(W / "units" / UNIT, "a") as f:
                f.write(f"{p.pid}\n")
        c.settimeout(30)
        while c.recv(4096):
            pass
    except OSError:
        pass
    finally:
        c.close()


def main() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((os.environ["FAKE_WEB_HOST"], int(os.environ["FAKE_WEB_PORT"])))
    srv.listen(16)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


if __name__ == "__main__":
    main()
