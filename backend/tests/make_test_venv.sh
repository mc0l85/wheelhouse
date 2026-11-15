#!/usr/bin/env bash
# Create the repo-local acceptance-test venv backend/.venv-test (gitignored).
#
#   backend/tests/make_test_venv.sh            create it if missing
#   backend/tests/make_test_venv.sh --fresh    delete and recreate
#
# Runtime packages come from backend/requirements.txt, constrained to the deployed venv's freeze
# (tests/constraints.txt), plus the pinned test tools (tests/requirements-test.txt). Needs network,
# so run it once OUTSIDE the network namespace the ledger checks run in. The ledger checks call
# backend/.venv-test/bin/python; they fail with "No such file" until this has run.
set -euo pipefail
BACKEND=$(cd "$(dirname "$0")/.." && pwd)
VENV="$BACKEND/.venv-test"
[ "${1:-}" = "--fresh" ] && rm -rf "$VENV"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install -q --disable-pip-version-check \
  -r "$BACKEND/requirements.txt" -r "$BACKEND/tests/requirements-test.txt" -c "$BACKEND/tests/constraints.txt"
"$VENV/bin/python" - <<'PY'
import fastapi, starlette, uvicorn, websockets, pytest
print("test venv:", "fastapi", fastapi.__version__, "starlette", starlette.__version__,
      "uvicorn", uvicorn.__version__, "websockets", websockets.__version__, "pytest", pytest.__version__)
PY
