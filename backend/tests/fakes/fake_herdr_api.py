"""Scriptable fake of herdr's control socket (unix socket, newline-JSON).

Wire shape (what herdr.py `_socket_request` and events.py speak):
  request : {"id": "web:<method>", "method": "<method>", "params": {...}}\\n
  reply   : {"id": "<same id>", "result": ...}\\n   or   {"id": ..., "error": ...}\\n
  events.subscribe: ack reply {"id":..., "result": {"type": "subscription_started"}}, then
            event lines {"event": "<name>", "data": {...}} pushed by the test.

Script per test:
  api.on("pane.list", result={"panes": [...]})
  api.on("pane.focus", error="no such pane")
  api.on("tab.list", handler=lambda params: {"tabs": []})
  api.on("pane.list", hang=True)         # accept, read the request, never reply
  api.on("pane.list", close=True)        # close without replying
  api.on("pane.list", delay=0.5, result=...)
  api.on("pane.list", raw=b"not json\\n")
  api.push_event("pane.agent_status_changed", {"pane_id": "w:1", "agent_status": "blocked"})
  api.drop_subscribers()
  api.on("events.subscribe", error="subscribe refused")   # rules win over the built-in ack
Every request is recorded in api.requests. Unscripted methods get an error reply.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from typing import Any, Callable


class FakeHerdrApi:
    def __init__(self, path: str):
        self.path = path
        self.requests: list[dict] = []
        self._rules: dict[str, dict] = {}
        self._subs: list[socket.socket] = []
        self._lock = threading.Lock()
        self._srv: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- scripting ---------------------------------------------------------
    def on(self, method: str, *, result: Any = None, error: Any = None,
           handler: Callable[[dict], Any] | None = None, delay: float = 0.0,
           hang: bool = False, close: bool = False, raw: bytes | None = None) -> None:
        self._rules[method] = dict(result=result, error=error, handler=handler, delay=delay,
                                   hang=hang, close=close, raw=raw)

    def push_event(self, event: str, data: dict | None = None) -> int:
        line = (json.dumps({"event": event, "data": data or {}}) + "\n").encode()
        sent = 0
        with self._lock:
            for c in list(self._subs):
                try:
                    c.sendall(line); sent += 1
                except OSError:
                    self._subs.remove(c)
        return sent

    def drop_subscribers(self) -> None:
        with self._lock:
            for c in self._subs:
                try:
                    c.close()
                except OSError:
                    pass
            self._subs.clear()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> "FakeHerdrApi":
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if os.path.exists(self.path):
            os.unlink(self.path)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(self.path); s.listen(16); s.settimeout(0.1)
        self._srv = s
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self.drop_subscribers()
        if self._srv:
            self._srv.close()
        if self._thread:
            self._thread.join(2)
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def __enter__(self): return self.start()
    def __exit__(self, *a): self.stop()

    # -- server ------------------------------------------------------------
    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                c, _ = self._srv.accept()           # type: ignore[union-attr]
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c: socket.socket) -> None:
        buf = b""
        try:
            while b"\n" not in buf:
                chunk = c.recv(65536)
                if not chunk:
                    c.close(); return
                buf += chunk
            req = json.loads(buf.split(b"\n", 1)[0])
        except (OSError, ValueError):
            c.close(); return
        self.requests.append(req)
        method, rid = req.get("method", ""), req.get("id")
        rule = self._rules.get(method)
        if rule and rule["delay"]:
            time.sleep(rule["delay"])
        try:
            if rule and rule["hang"]:
                self._stop.wait(30); c.close(); return
            if rule and rule["close"]:
                c.close(); return
            if rule and rule["raw"] is not None:
                c.sendall(rule["raw"]); c.close(); return
            if method == "events.subscribe" and rule is None:
                c.sendall((json.dumps({"id": rid, "result": {"type": "subscription_started"}}) + "\n").encode())
                with self._lock:
                    self._subs.append(c)
                return                              # stays open; push_event writes to it
            if rule is None:
                reply = {"id": rid, "error": f"unscripted method {method}"}
            elif rule["error"] is not None:
                reply = {"id": rid, "error": rule["error"]}
            else:
                res = rule["handler"](req.get("params") or {}) if rule["handler"] else rule["result"]
                reply = {"id": rid, "result": res}
            c.sendall((json.dumps(reply) + "\n").encode())
            c.close()
        except OSError:
            pass
