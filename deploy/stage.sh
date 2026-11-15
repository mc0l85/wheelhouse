#!/usr/bin/env bash
# Stage a release for deploy/cutover.sh: the committed backend at HEAD plus a built frontend.
#
#   deploy/stage.sh --static DIR --stage DIR [--repo DIR]
#
#   --static DIR   the built frontend (vite output, must hold index.html). The build runs elsewhere
#                  (frontend/e2e/build_remote.sh on the build VM); this script never builds it.
#   --stage DIR    where to write the tree. It must not exist, or be empty. Nothing is ever deleted.
#   --repo DIR     the repo whose HEAD is staged (default: the repo this script lives in).
#
# Result (the layout cutover.sh expects as STAGE_DIR):
#   DIR/backend/app  DIR/backend/static  DIR/backend/requirements.txt  DIR/REVISION (the commit sha)
# Only committed content is staged (git archive HEAD), so uncommitted edits never ship. The tree is
# built next to DIR and moved into place at the end; a failure leaves DIR.partial.<pid> for you to look at.
set -euo pipefail

die() { echo "stage.sh: $*" >&2; exit 2; }

STATIC="" STAGE="" REPO=""
while [ $# -gt 0 ]; do
  case "$1" in
    --static) STATIC=${2:-}; shift 2 || die "--static needs a value";;
    --stage)  STAGE=${2:-};  shift 2 || die "--stage needs a value";;
    --repo)   REPO=${2:-};   shift 2 || die "--repo needs a value";;
    -h|--help) sed -n '2,15p' "$0"; exit 0;;
    *) die "unknown argument: $1";;
  esac
done
[ -n "$STATIC" ] && [ -n "$STAGE" ] || die "usage: stage.sh --static DIR --stage DIR [--repo DIR]"
[ -n "$REPO" ] || REPO=$(cd "$(dirname "$0")/.." && pwd)

[ -d "$STATIC" ] && [ -f "$STATIC/index.html" ] || die "--static must be a built frontend with index.html: $STATIC"
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repo: $REPO"
SHA=$(git -C "$REPO" rev-parse --verify HEAD) || die "no HEAD in $REPO"
for p in backend/app/main.py backend/requirements.txt; do
  git -C "$REPO" cat-file -e "HEAD:$p" 2>/dev/null || die "HEAD has no $p"
done
if [ -e "$STAGE" ]; then
  [ -d "$STAGE" ] || die "--stage exists and is not a directory: $STAGE"
  [ -z "$(ls -A "$STAGE")" ] || die "--stage is not empty (nothing is deleted; pick a new path): $STAGE"
fi
if [ -n "$(git -C "$REPO" status --porcelain -- backend/app backend/requirements.txt)" ]; then
  echo "stage.sh: warning: uncommitted changes under backend/app or requirements.txt are NOT staged" >&2
fi

PARTIAL="$STAGE.partial.$$"
mkdir -p "$(dirname "$STAGE")"
mkdir "$PARTIAL"
git -C "$REPO" archive --format=tar HEAD backend/app backend/requirements.txt | tar -x -C "$PARTIAL"
mkdir "$PARTIAL/backend/static"
cp -R "$STATIC"/. "$PARTIAL/backend/static/"
printf '%s\n' "$SHA" > "$PARTIAL/REVISION"
[ -f "$PARTIAL/backend/static/index.html" ] && [ -f "$PARTIAL/backend/app/main.py" ] \
  && [ -f "$PARTIAL/backend/requirements.txt" ] || die "staged tree is incomplete: $PARTIAL"
chmod -R u+rwX,go+rX "$PARTIAL"
[ -d "$STAGE" ] && rmdir "$STAGE"        # an empty directory we were allowed to reuse
mv "$PARTIAL" "$STAGE"
echo "staged $SHA at $STAGE"
