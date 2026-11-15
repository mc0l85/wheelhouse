#!/usr/bin/env bash
# Install (idempotent) the pinned Python Playwright and its WebKit build on the build VM, over the
# ssh path build_remote.sh uses. WebKit's OS libraries come from `playwright install-deps webkit`
# (apt, needs passwordless sudo on the VM). Run once; check_vm.sh refuses to run without it.
#
#   HW_BUILD_ENV=<env file> frontend/e2e/setup_vm_webkit.sh
set -euo pipefail
[ -n "${HW_BUILD_ENV:-}" ] && [ -r "${HW_BUILD_ENV:-}" ] || { echo "setup_vm_webkit.sh: HW_BUILD_ENV must name a readable env file" >&2; exit 2; }
set -a
# shellcheck disable=SC1090
source "$HW_BUILD_ENV"
set +a
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=vm_ssh.sh
. "$HERE/vm_ssh.sh"
vm "set -e; [ -x ~/$VM_VENV/bin/python ] || python3 -m venv ~/$VM_VENV
~/$VM_VENV/bin/pip install -q --disable-pip-version-check '$PLAYWRIGHT_PIN'
sudo -n ~/$VM_VENV/bin/playwright install-deps webkit >/dev/null
~/$VM_VENV/bin/playwright install webkit
~/$VM_VENV/bin/python -c 'import importlib.metadata as m; print(\"playwright\", m.version(\"playwright\"))'
cd ~/.cache/ms-playwright && ls -d webkit-*"
