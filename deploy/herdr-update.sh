#!/usr/bin/env bash
# Nightly herdr maintenance (the user timer herdr-update.timer runs this; deploy/cutover.sh installs it
# at the host's UPDATE_SCRIPT). PRD Decision 10: plain `herdr update`, NEVER a live handoff -- a live
# handoff ends herdr.service (KillMode=mixed) and every pane with it. The running server keeps the old
# binary; the next server start picks up the new one.
#
#   1. (re)install agent hooks whose `herdr integration status` line says "not installed" / "outdated"
#   2. wait until every pane is idle ($HERDR_WAIT_IDLE, default ~/.local/bin/herdr-wait-idle.py);
#      if it fails or is missing, skip ONLY the binary update (fail closed). HERDR_SKIP_WAIT=1 skips it.
#   3. herdr update (plain), then re-check the hooks (a new herdr ships newer hook versions)
#
# Env: HERDR_BIN (default ~/.local/bin/herdr), HERDR_TARGETS (default "claude pi opencode antigravity-cli"),
#      HERDR_WAIT_IDLE, HERDR_SKIP_WAIT, HERDR_QUIET_SECS (passed to the wait script as --quiet-secs).
# Exit: 0 ok (also when the update was skipped for busy panes), 1 a hook install or the update failed.
set -u

HERDR_BIN="${HERDR_BIN:-$HOME/.local/bin/herdr}"
HERDR_TARGETS="${HERDR_TARGETS:-claude pi opencode antigravity-cli}"
HERDR_WAIT_IDLE="${HERDR_WAIT_IDLE:-$HOME/.local/bin/herdr-wait-idle.py}"
rc=0

log() { printf 'herdr-update: %s\n' "$*" >&2; }

install_hooks() {
  local status target line
  status="$("$HERDR_BIN" integration status 2>&1)" || { log "integration status failed"; rc=1; return; }
  for target in $HERDR_TARGETS; do
    line="$(printf '%s\n' "$status" | grep -E "^${target}:" | head -n 1)"
    case "$line" in
      *"not installed"*|*outdated*)
        log "hook $target: ${line#*: } -> install"
        "$HERDR_BIN" integration install "$target" || { log "hook install failed: $target"; rc=1; };;
    esac
  done
}

install_hooks

proceed=1
if [ "${HERDR_SKIP_WAIT:-0}" != 1 ]; then
  if [ ! -x "$HERDR_WAIT_IDLE" ]; then
    log "no idle-wait script at $HERDR_WAIT_IDLE: skipping the binary update"
    proceed=0
  else
    wait_args=()
    [ -n "${HERDR_QUIET_SECS:-}" ] && wait_args=(--quiet-secs "$HERDR_QUIET_SECS")
    if ! "$HERDR_WAIT_IDLE" ${wait_args[@]+"${wait_args[@]}"}; then
      log "panes are not idle: skipping the binary update"
      proceed=0
    fi
  fi
fi

if [ "$proceed" = 1 ]; then
  "$HERDR_BIN" update || { log "herdr update failed"; rc=1; }
  install_hooks
fi
exit "$rc"
