#!/usr/bin/env bash
# Build and install herdr + herdr web as system units (uses sudo to install them).
#
#   deploy/install.sh            build + install the system units + start them
#   deploy/install.sh --build    build only (backend venv + frontend bundle)
#
# Needs: herdr >= 0.8 on PATH, python3 >= 3.10 with venv, node >= 18 + npm.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONF_DIR="$HOME/.config/herdr-web"
UNIT_DIR="/etc/systemd/system"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"
SERVICE_GROUP="${SERVICE_GROUP:-$(id -gn)}"
SERVICE_HOME="$(getent passwd "$SERVICE_USER" | cut -d: -f6)"
SUDO=""; [ "$(id -u)" -eq 0 ] || SUDO="sudo"

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
  echo "    sudo systemctl restart herdr-web"
fi

case "$APP_DIR$CONF_DIR" in *[[:space:]\|\&]*) echo "install paths must not contain spaces, | or &"; exit 1;; esac
HERDR_BIN="$(command -v herdr || echo "$HOME/.local/bin/herdr")"
render() {
  sed -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@CONF_DIR@|$CONF_DIR|g" -e "s|@HERDR_BIN@|$HERDR_BIN|g" \
      -e "s|@SERVICE_USER@|$SERVICE_USER|g" -e "s|@SERVICE_GROUP@|$SERVICE_GROUP|g" \
      -e "s|@SERVICE_HOME@|$SERVICE_HOME|g" "$APP_DIR/deploy/$1" | $SUDO tee "$UNIT_DIR/$1" >/dev/null
}
say "services: $UNIT_DIR/herdr.service, $UNIT_DIR/herdr-web.service"
render herdr.service
render herdr-web.service
$SUDO systemctl daemon-reload
$SUDO systemctl enable herdr.service herdr-web.service
$SUDO systemctl restart herdr-web.service
$SUDO systemctl start herdr.service
sleep 2
systemctl is-active --quiet herdr-web.service ||
  { echo "service failed to start:"; journalctl -u herdr-web.service -n 20 --no-pager; exit 1; }

set -a; . "$CONF_DIR/env"; set +a
say "running on http://${HERDRWEB_HOST}:${HERDRWEB_PORT}"
echo "    phone access + TLS + push:     docs/NETWORKING.md (add your host name to HERDRWEB_ALLOWED_HOSTS)"
