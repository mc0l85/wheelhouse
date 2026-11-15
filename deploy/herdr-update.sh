#!/usr/bin/env bash
# Nightly herdr maintenance (the user timer herdr-update.timer runs this; deploy/cutover.sh installs it
# at the host's UPDATE_SCRIPT). PRD Decision 10: plain `herdr update`, NEVER `--handoff` -- a live
# handoff ends herdr.service (KillMode=mixed) and every pane with it. The running server keeps the old
# binary; the next server start picks up the new one.
#
#   1. (re)install agent hooks whose `herdr integration status` line says "not installed" / "outdated"
#   2. wait until every pane is idle ($HERDR_WAIT_IDLE, default ~/.local/bin/herdr-wait-idle.py);
#      if it fails or is missing, skip ONLY the binary update (fail closed). HERDR_SKIP_WAIT=1 skips it.
#   3. herdr update (no --handoff), then re-check the hooks (a new herdr ships newer hook versions)
#
# Env: HERDR_BIN (default ~/.local/bin/herdr), HERDR_TARGETS (default "claude pi opencode antigravity-cli"),
#      HERDR_WAIT_IDLE, HERDR_SKIP_WAIT, HERDR_QUIET_SECS (passed to the wait script as --quiet-secs).
# Exit: 0 ok (also when the update was skipped for busy panes), 1 a hook install or the update failed.
set -u
echo "herdr-update.sh: todo" >&2
exit 1
