"""herdr-web configuration — all overridable via environment.

herdr-web is a browser wrapper over a LIVE herdr terminal-workspace server on
this host. Apart from push subscriptions it owns no state: everything it serves
comes from the herdr CLI (JSON envelopes) or herdr's control socket. Mutating
actions go straight to the live session, so they are off unless
HERDRWEB_ENABLE_WRITES=1.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field


def _csv(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    # --- the herdr server we wrap ---
    herdr_bin: str = os.environ.get("HERDR_BIN") or shutil.which("herdr") or "herdr"
    # herdr targets a named session; "default" is herdr's own persistent one.
    herdr_session: str = os.environ.get("HERDR_SESSION", "default")
    # herdr's config dir; the session's control socket and session.json live
    # here ("default") or under sessions/<name>/.
    herdr_home: str = os.path.expanduser(os.environ.get("HERDR_HOME", "~/.config/herdr"))
    cli_timeout: float = float(os.environ.get("HERDR_CLI_TIMEOUT", "15"))

    # --- the herdr-web service itself ---
    host: str = os.environ.get("HERDRWEB_HOST", "127.0.0.1")
    port: int = int(os.environ.get("HERDRWEB_PORT", "8086"))
    # Host names this service answers to (DNS-rebinding guard). Add the name or
    # address you reach it by, e.g. "herdr.example.com,192.0.2.10". "*" = any.
    allowed_hosts: list[str] = field(default_factory=lambda: _csv(
        "HERDRWEB_ALLOWED_HOSTS", "localhost,127.0.0.1,::1"))
    # Cross-origin readers (e.g. a Vite dev server). Empty = same-origin only.
    cors_origins: list[str] = field(default_factory=lambda: _csv("HERDRWEB_CORS", ""))
    # Concurrent terminal WebSockets (each one is a herdr client process).
    max_terminals: int = int(os.environ.get("HERDRWEB_MAX_TERMINALS", "8"))
    # Feature flag: when False every mutating endpoint returns 403. Ship safe.
    enable_writes: bool = os.environ.get("HERDRWEB_ENABLE_WRITES", "0") == "1"
    # Extra browser origins allowed to open the WebSockets / call mutating
    # endpoints (e.g. a dev server). Same-origin is always allowed. There is no
    # login: reach this service only over a trusted network (see docs/NETWORKING.md).
    allowed_origins: list[str] = field(default_factory=lambda: _csv("HERDRWEB_ALLOWED_ORIGINS", ""))
    # Web Push: where the VAPID key + subscriptions live, the contact sent to
    # push services (mailto: or https: URL), and whether to hide pane names.
    state_dir: str = os.environ.get("HERDRWEB_STATE_DIR", "~/.local/state/herdr-web")
    push_contact: str = os.environ.get("HERDRWEB_PUSH_CONTACT", "mailto:admin@example.com")
    # Extra push endpoints to accept beyond the big browser push services
    # (host or host:port, any scheme) — for self-hosted push or tests.
    push_extra_hosts: list[str] = field(default_factory=lambda: _csv("HERDRWEB_PUSH_HOSTS", ""))
    push_max_subs: int = int(os.environ.get("HERDRWEB_PUSH_MAX_SUBS", "20"))
    push_private: bool = os.environ.get("HERDRWEB_PUSH_PRIVATE", "0") == "1"
    # Built SPA (vite build output). Served as static when present.
    static_dir: str = os.environ.get(
        "HERDRWEB_STATIC", os.path.join(os.path.dirname(__file__), "..", "static"))


settings = Settings()
