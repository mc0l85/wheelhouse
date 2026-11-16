#!/usr/bin/env bash
# herdr cutover (PRD 4.3): move the herdr server out of the web unit's cgroup into herdr.service,
# deploy the new backend + frontend, and bring every agent back with its conversation.
#
#   deploy/cutover.sh --host-env FILE [--dry-run] [--force] [--rollback] [--foreground] [--coordinator PANE]
#
#   --host-env FILE   this host's values (KEY=VALUE, kept OUTSIDE the repo): SERVICE_USER SERVICE_GROUP
#                     SERVICE_HOME HERDR_BIN HERDR_HOME APP_DIR CONF_DIR UNIT_DIR HERDR_UNIT WEB_UNIT
#                     OLD_WEB_UNIT STAGE_DIR STATE_DIR UPDATE_SCRIPT HERDR_AS_USER NOTIFY_CMD HOST_NAME
#                     IDLE_WAIT_S QUIT_WAIT_S RELAUNCH_WAIT_S SOCKET_WAIT_S. No value is hard-coded here.
#   --dry-run         step 1 only, read-only: print {"manifest": ..., "plan": [...]} and write nothing.
#   --force           pass the idle gate even with blocked / still-working agents.
#   --rollback        undo the latest run (STATE_DIR/latest): old tree, old unit, old nightly script.
#   --foreground      run the steps here. Without it (and without --dry-run / --rollback) the script only
#                     re-executes itself detached: systemd-run --unit=herdr-cutover ... --foreground,
#                     so stopping the web unit cannot kill the cutover (or the coordinator's pane).
#   --coordinator P   the pane that launched the cutover: quit last, relaunched last, resumed last.
#                     Default: $HERDR_PANE_ID, which sudo drops: as root it is then required, or pass
#                     --no-coordinator when launching from outside herdr.
#
# Exit: 0 done | 2 usage / config / preflight (nothing touched) | 3 aborted before step 4 (nothing
# changed) | 4 failed in step 5 or 6 and rolled back | 5 finished, problems recorded in steps 7-9.
#
# Steps (PRD 4.3): 1 manifest, 2 idle gate, 3 quit each agent (coordinator last), 4 stop the web unit,
# 5 deploy + start the web unit + prove the guard, 6 start herdr.service, 7 relaunch, 8 resume,
# 9 verify + report. Rollback after step 4. The full contract (state files, manifest and result
# schemas) is pinned in backend/tests/acceptance/cutover_world.py.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SELF="$HERE/$(basename "$0")"
for a in "$@"; do
  case "$a" in -h|--help) sed -n '2,24p' "$0"; exit 0;; esac
done
command -v python3 >/dev/null || { echo "cutover.sh: python3 is required" >&2; exit 2; }
# All logic lives in cutover_lib.py (stdlib only); this launcher only locates it.
exec python3 "$HERE/cutover_lib.py" --self "$SELF" "$@"
