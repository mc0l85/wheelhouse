#!/usr/bin/env bash
# Dev runner for the herdr-web backend. Creates a venv on first run.
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -U pip
  ./.venv/bin/pip install -r requirements.txt
fi

# Writes OFF by default — flip to drive the live session from the browser.
export HERDRWEB_ENABLE_WRITES="${HERDRWEB_ENABLE_WRITES:-0}"
export HERDRWEB_PORT="${HERDRWEB_PORT:-8086}"

exec ./.venv/bin/uvicorn app.main:app --reload \
  --host "${HERDRWEB_HOST:-127.0.0.1}" --port "$HERDRWEB_PORT"
