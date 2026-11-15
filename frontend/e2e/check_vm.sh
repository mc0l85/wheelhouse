#!/usr/bin/env bash
# Ledger check for the phone items V-1..V-3: build the frontend on the build VM (build_remote.sh),
# then run acceptance_v.py THERE, in the VM's pinned Playwright WebKit (setup_vm_webkit.sh), with
# an iPhone profile. Nothing browser-shaped runs on the dev host. Screenshots come back to
# frontend/e2e/results/shots/<item-ids>/ (gitignored) for a later model review.
#
#   frontend/e2e/check_vm.sh V-1 [V-2 V-3]
#
# HW_BUILD_ENV must name a readable env file (outside the repo) exporting the BUILD_* variables.
# Exit 2: environment missing or the VM's Playwright/WebKit is not the pinned one.
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: $0 <item-id>..." >&2; exit 2; }
if [ -z "${HW_BUILD_ENV:-}" ] || [ ! -r "${HW_BUILD_ENV:-}" ]; then
  echo "check_vm.sh: HW_BUILD_ENV must name a readable env file with the BUILD_* variables" >&2
  exit 2
fi
set -a
# shellcheck disable=SC1090
source "$HW_BUILD_ENV"
set +a
HERE=$(cd "$(dirname "$0")" && pwd)
FE=$(dirname "$HERE")
# shellcheck source=vm_ssh.sh
. "$HERE/vm_ssh.sh"
IDS=$(IFS=,; echo "$*")
TAG="accv-$(basename "$(git -C "$FE" rev-parse --show-toplevel)")"
RDIR=$BUILD_REMOTE_DIR/$TAG

pin=$(vm "~/$VM_VENV/bin/python -c 'import importlib.metadata as m; print(m.version(\"playwright\"))' 2>/dev/null; cd ~/.cache/ms-playwright 2>/dev/null && ls -d webkit-* 2>/dev/null" || true)
case "$pin" in
  "${PLAYWRIGHT_PIN#playwright==}"*"$WEBKIT_DIR_PIN"*) ;;
  *) echo "check_vm.sh: the VM's Playwright/WebKit is not $PLAYWRIGHT_PIN / $WEBKIT_DIR_PIN (got: $(echo $pin)); run frontend/e2e/setup_vm_webkit.sh" >&2
     exit 2;;
esac

OUT=$(mktemp -d "${TMPDIR:-/tmp}/hw-accv-dist.XXXXXX")
trap 'rm -rf "$OUT"' EXIT
"$HERE/build_remote.sh" "$FE" "$OUT" "$TAG" >&2           # leaves the build at ~/$RDIR/dist on the VM
SHOTS="e2e/results/shots/$(echo "$IDS" | tr ',' '_')"
rc=0
vm "cd ~/$RDIR && rm -rf $SHOTS && ~/$VM_VENV/bin/python e2e/acceptance_v.py dist --items $IDS --shots $SHOTS --out $SHOTS/result.json" || rc=$?
mkdir -p "$FE/$SHOTS"
rsync -az --delete -e "ssh ${SSHO[*]}" "$BUILD_SSH_TARGET:$RDIR/$SHOTS/" "$FE/$SHOTS/" >&2 || echo "check_vm.sh: could not fetch the screenshots" >&2
exit $rc
