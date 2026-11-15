#!/usr/bin/env bash
# Build the frontend on a separate build VM (production `vite build` must not run on the dev host).
#   e2e/build_remote.sh <frontend-src-dir> <dist-out-dir> <tag> [VITE_INPUT_TRACE=1]
#
# Every host value comes from the environment; there are no defaults:
#   BUILD_SSH_JUMP    ssh jump host, user@host            (ssh -J)
#   BUILD_SSH_PORT    ssh port of the build VM as seen from the jump host
#   BUILD_SSH_TARGET  ssh target, user@host of the build VM
#   BUILD_NODE_DIR    directory holding node/npm ON THE VM (expanded there, may use $HOME)
#   BUILD_REMOTE_DIR  base directory on the VM, relative to its home; the build lands in <base>/<tag>
# Optional: BUILD_SSH_CM_DIR  local dir for the ssh control socket and known_hosts.
set -euo pipefail
usage() { sed -n '2,13p' "$0" >&2; exit 2; }
[ $# -ge 3 ] || usage
for v in BUILD_SSH_JUMP BUILD_SSH_PORT BUILD_SSH_TARGET BUILD_NODE_DIR BUILD_REMOTE_DIR; do
  [ -n "${!v:-}" ] || { echo "build_remote.sh: $v is not set" >&2; usage; }
done
SRC=$(realpath "$1"); OUT=$2; TAG=$3; TRACE=${4:-}
CM_DIR=${BUILD_SSH_CM_DIR:-${TMPDIR:-/tmp}/build-ssh-cm}; mkdir -p "$CM_DIR"
SSHO=(-o BatchMode=yes -o ControlMaster=auto -o "ControlPath=$CM_DIR/cm-%C" -o ControlPersist=10m
      -o HostKeyAlias=build-vm -o "UserKnownHostsFile=$CM_DIR/known_hosts"
      -o StrictHostKeyChecking=accept-new -J "$BUILD_SSH_JUMP" -p "$BUILD_SSH_PORT")
RDIR=$BUILD_REMOTE_DIR/$TAG
ssh "${SSHO[@]}" "$BUILD_SSH_TARGET" "mkdir -p ~/$RDIR"
rsync -az --delete --exclude node_modules --exclude dist --exclude e2e/results \
  -e "ssh ${SSHO[*]}" "$SRC/" "$BUILD_SSH_TARGET:$RDIR/"
ssh "${SSHO[@]}" "$BUILD_SSH_TARGET" "export PATH=$BUILD_NODE_DIR:\$PATH; cd ~/$RDIR && npm ci --no-audit --no-fund >/dev/null && $TRACE npm run build"
mkdir -p "$OUT"
rsync -az --delete -e "ssh ${SSHO[*]}" "$BUILD_SSH_TARGET:$RDIR/dist/" "$OUT/"
echo "built $TAG -> $OUT"
