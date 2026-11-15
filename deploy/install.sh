#!/usr/bin/env bash
# Build and install herdr + herdr web as system units (uses sudo to install them).
#
#   deploy/install.sh            build + install the system units + start them
#   deploy/install.sh --build    build only (backend venv + frontend bundle)
#
# Needs: herdr >= 0.8 on PATH, python3 >= 3.10 with venv, node >= 18 + npm.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="/etc/systemd/system"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"
SERVICE_GROUP="${SERVICE_GROUP:-$(id -gn "$SERVICE_USER")}"
PW="$(getent passwd "$SERVICE_USER")" || { echo "getent: no such user: $SERVICE_USER"; exit 1; }
SERVICE_HOME="$(printf '%s' "$PW" | cut -d: -f6)"
SERVICE_UID="$(printf '%s' "$PW" | cut -d: -f3)"
[ "$SERVICE_UID" -ne 0 ] || { echo "refusing to run the services as root (SERVICE_USER=$SERVICE_USER, uid 0)"; exit 1; }
[ -n "$SERVICE_HOME" ] && [ -d "$SERVICE_HOME" ] || { echo "home directory of $SERVICE_USER does not exist: '$SERVICE_HOME'"; exit 1; }
CONF_DIR="$SERVICE_HOME/.config/herdr-web"
SUDO=""; [ "$(id -u)" -eq 0 ] || SUDO="sudo"

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null || { echo "missing: $1 ($2)"; exit 1; }; }

need python3 "python 3.10+"
need npm "node 18+"
HERDR_BIN="$(command -v herdr || true)"
[ -n "$HERDR_BIN" ] || HERDR_BIN="$SERVICE_HOME/.local/bin/herdr"
[ -x "$HERDR_BIN" ] || { echo "missing: herdr — install it first: https://herdr.dev"; exit 1; }

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

# The herdr unit runs the default session's socket; a different session or home
# in the env file would point herdr-web at a server that unit does not manage.
if awk -F= '/^[[:space:]]*HERDR_(SESSION|HOME)[[:space:]]*=/{v=$2; gsub(/["'"'"'[:space:]]/,"",v); if (v!="" && v!="default" && v!="~/.config/herdr") bad=1} END{exit !bad}' "$CONF_DIR/env"; then
  echo "$CONF_DIR/env sets a non-default HERDR_SESSION or HERDR_HOME."
  echo "herdr.service runs the default session; remove those lines (or edit the unit) and re-run."
  exit 1
fi

# A herdr server started by hand (outside herdr.service) would own the socket
# and the unit's server could not start. Refuse rather than kill someone's panes.
for d in /proc/[0-9]*; do
  [ -r "$d/cmdline" ] || continue
  [ "$(stat -c %u "$d" 2>/dev/null)" = "$SERVICE_UID" ] || continue
  args="$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null || true)"
  case "$args" in
    "$HERDR_BIN server"*|*"/herdr server"*|"herdr server"*)
      if ! tr '\0' '\n' < "$d/cgroup" 2>/dev/null | awk '/herdr\.service/{f=1} END{exit !f}'; then
        echo "a herdr server (pid ${d#/proc/}) is running for $SERVICE_USER outside herdr.service."
        echo "Stop it yourself (it holds your panes), then re-run this installer."
        exit 1
      fi;;
  esac
done

case "$APP_DIR$CONF_DIR" in *[[:space:]\|\&]*) echo "install paths must not contain spaces, | or &"; exit 1;; esac
render() {
  sed -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@CONF_DIR@|$CONF_DIR|g" -e "s|@HERDR_BIN@|$HERDR_BIN|g" \
      -e "s|@SERVICE_USER@|$SERVICE_USER|g" -e "s|@SERVICE_GROUP@|$SERVICE_GROUP|g" \
      -e "s|@SERVICE_UID@|$SERVICE_UID|g" -e "s|@SERVICE_HOME@|$SERVICE_HOME|g" "$APP_DIR/deploy/$1" | $SUDO tee "$UNIT_DIR/$1" >/dev/null
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
  { echo "service failed to start:"; sudo journalctl -u herdr-web.service -n 20 --no-pager; exit 1; }

set -a; . "$CONF_DIR/env"; set +a
say "running on http://${HERDRWEB_HOST}:${HERDRWEB_PORT}"
echo "    herdr needs a per-user runtime dir: run once  sudo loginctl enable-linger $SERVICE_USER"
echo "    phone access + TLS + push:     docs/NETWORKING.md (add your host name to HERDRWEB_ALLOWED_HOSTS)"
