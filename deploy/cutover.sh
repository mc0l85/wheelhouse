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
#                     Default: $HERDR_PANE_ID.
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
HOST_ENV="" DRY_RUN=0 FORCE=0 ROLLBACK=0 FOREGROUND=0 COORDINATOR="${HERDR_PANE_ID:-}"
ARGS=("$@")

usage() { sed -n '2,24p' "$0" >&2; exit 2; }
todo() { echo "cutover.sh: todo: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --host-env) HOST_ENV=${2:-}; shift 2 || usage;;
    --dry-run) DRY_RUN=1; shift;;
    --force) FORCE=1; shift;;
    --rollback) ROLLBACK=1; shift;;
    --foreground) FOREGROUND=1; shift;;
    --coordinator) COORDINATOR=${2:-}; shift 2 || usage;;
    -h|--help) usage;;
    *) echo "cutover.sh: unknown argument: $1" >&2; usage;;
  esac
done
[ -n "$HOST_ENV" ] || usage

load_host_env() { todo "load_host_env: parse $HOST_ENV (KEY=VALUE, no expansion), check required keys"; }
preflight()     { todo "preflight: stage tree + REVISION, CONF_DIR/env keys, units, HERDR_BIN; exit 2 on failure"; }
detach()        { todo "detach: systemd-run --unit=herdr-cutover ... ${ARGS[*]} --foreground"; }
step1_manifest()  { todo "step 1: manifest (copies, pane list, agent list, process-info, /proc cmdline)"; }
step2_idle_gate() { todo "step 2: idle gate (blocked -> abort; working -> wait <= IDLE_WAIT_S; --force)"; }
step3_quit()      { todo "step 3: send-keys ctrl+c ctrl+c per agent, coordinator last; SIGTERM fallback"; }
step4_stop_web()  { todo "step 4: stop OLD_WEB_UNIT; no herdr server, socket refuses; session.json check"; }
step5_deploy()    { todo "step 5: deploy staged tree; render + start WEB_UNIT; retire OLD_WEB_UNIT; prove guard"; }
step6_herdr()     { todo "step 6: render HERDR_UNIT; install herdr-update.sh; start; socket, MainPID, panes"; }
step7_relaunch()  { todo "step 7: relaunch per manifest row (claude --resume / pi --session / fresh)"; }
step8_resume()    { todo "step 8: wait idle, send resume + Enter; blocked -> skip; coordinator last"; }
step9_verify()    { todo "step 9: result.json, restart WEB_UNIT once, notify"; }
rollback()        { todo "rollback: stop units, restore old tree / old unit / old update script, start old unit"; }

main() {
  load_host_env
  if [ "$ROLLBACK" = 1 ]; then rollback; return; fi
  preflight
  if [ "$DRY_RUN" = 0 ] && [ "$FOREGROUND" = 0 ]; then detach; return; fi
  step1_manifest
  [ "$DRY_RUN" = 1 ] && return
  step2_idle_gate
  step3_quit
  step4_stop_web
  step5_deploy
  step6_herdr
  step7_relaunch
  step8_resume
  step9_verify
}
main
