#!/usr/bin/env bash
# Build and install herdr web for the current user (no root needed).
#
#   deploy/install.sh            build + install + start the user service
#   deploy/install.sh --build    build only (backend venv + frontend bundle)
#
# Needs: herdr >= 0.8 on PATH, python3 >= 3.10 with venv, node >= 18 + npm.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONF_DIR="$HOME/.config/herdr-web"
UNIT_DIR="$HOME/.config/systemd/user"

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null || { echo "missing: $1 ($2)"; exit 1; }; }

need python3 "python 3.10+"
need npm "node 18+"
command -v herdr >/dev/null || [ -x "$HOME/.local/bin/herdr" ] ||
  { echo "missing: herdr — install it first: https://herdr.dev"; exit 1; }

say "backend: virtualenv + dependencies"
python3 -m venv "$APP_DIR/backend/.venv"
"$APP_DIR/backend/.venv/bin/pip" install -q -U pip
"$APP_DIR/backend/.venv/bin/pip" install -q -r "$APP_DIR/backend/requirements.txt"

say "frontend: build"
(cd "$APP_DIR/frontend" && npm ci --no-audit --no-fund && npm run build)
rm -rf "$APP_DIR/backend/static"
cp -r "$APP_DIR/frontend/dist" "$APP_DIR/backend/static"

[ "${1:-}" = "--build" ] && { say "built (not installed)"; exit 0; }

say "config: $CONF_DIR/env"
mkdir -p "$CONF_DIR"
if [ ! -f "$CONF_DIR/env" ]; then
  cp "$APP_DIR/backend/.env.example" "$CONF_DIR/env"
  echo "    created with safe defaults (127.0.0.1, read-only) — edit it, then:"
  echo "    systemctl --user restart herdr-web"
fi

say "service: $UNIT_DIR/herdr-web.service"
mkdir -p "$UNIT_DIR"
case "$APP_DIR" in *[[:space:]\|\&]*) echo "install path must not contain spaces, | or &: $APP_DIR"; exit 1;; esac
sed "s|@APP_DIR@|$APP_DIR|g" "$APP_DIR/deploy/herdr-web.service" > "$UNIT_DIR/herdr-web.service"
systemctl --user daemon-reload
systemctl --user enable herdr-web
systemctl --user restart herdr-web
sleep 2
systemctl --user is-active --quiet herdr-web ||
  { echo "service failed to start:"; journalctl --user -u herdr-web -n 20 --no-pager; exit 1; }

set -a; . "$CONF_DIR/env"; set +a
say "running on http://${HERDRWEB_HOST}:${HERDRWEB_PORT}"
echo "    keep it running after logout:  loginctl enable-linger \"$USER\""
echo "    phone access + TLS + push:     docs/NETWORKING.md (add your host name to HERDRWEB_ALLOWED_HOSTS)"
