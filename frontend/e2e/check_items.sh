#!/usr/bin/env bash
# Ledger check for the e2e acceptance items: build the frontend on the build VM (build_remote.sh),
# then run the named items' scenarios from acceptance_e2e.py against that build. Fully local
# apart from the build VM: the page is served on 127.0.0.1 with every /api route mocked.
#
#   frontend/e2e/check_items.sh C-2 [C-15 D-6 ...]
#
# HW_BUILD_ENV must name a readable env file that exports the BUILD_* variables build_remote.sh
# needs (it is sourced; keep it outside the repo). Exit 2 if it is missing.
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: $0 <item-id>..." >&2; exit 2; }
if [ -z "${HW_BUILD_ENV:-}" ]; then
  echo "check_items.sh: HW_BUILD_ENV is not set — point it at the env file with the BUILD_* variables for build_remote.sh" >&2
  exit 2
fi
[ -r "$HW_BUILD_ENV" ] || { echo "check_items.sh: HW_BUILD_ENV file is not readable: $HW_BUILD_ENV" >&2; exit 2; }
set -a
# shellcheck disable=SC1090
source "$HW_BUILD_ENV"
set +a
HERE=$(cd "$(dirname "$0")" && pwd)
FE=$(dirname "$HERE")
TAG="acc-$(basename "$(git -C "$FE" rev-parse --show-toplevel)")"
OUT=$(mktemp -d "${TMPDIR:-/tmp}/hw-acc-dist.XXXXXX")
trap 'rm -rf "$OUT"' EXIT
"$HERE/build_remote.sh" "$FE" "$OUT" "$TAG" >&2
python3 "$HERE/acceptance_e2e.py" "$OUT" --items "$(IFS=,; echo "$*")"
