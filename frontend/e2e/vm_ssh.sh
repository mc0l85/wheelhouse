#!/usr/bin/env bash
# Shared ssh options for the build VM (sourced by setup_vm_webkit.sh and check_vm.sh).
# Same connection as build_remote.sh: every host value comes from the BUILD_* environment.
for v in BUILD_SSH_JUMP BUILD_SSH_PORT BUILD_SSH_TARGET BUILD_NODE_DIR BUILD_REMOTE_DIR; do
  [ -n "${!v:-}" ] || { echo "vm_ssh.sh: $v is not set" >&2; exit 2; }
done
CM_DIR=${BUILD_SSH_CM_DIR:-${TMPDIR:-/tmp}/build-ssh-cm}; mkdir -p "$CM_DIR"
SSHO=(-o BatchMode=yes -o ControlMaster=auto -o "ControlPath=$CM_DIR/cm-%C" -o ControlPersist=10m
      -o HostKeyAlias=build-vm -o "UserKnownHostsFile=$CM_DIR/known_hosts"
      -o StrictHostKeyChecking=accept-new -J "$BUILD_SSH_JUMP" -p "$BUILD_SSH_PORT")
vm() { ssh "${SSHO[@]}" "$BUILD_SSH_TARGET" "$@"; }
# The e2e venv on the VM (Python Playwright + WebKit), outside any build dir so builds never wipe it.
VM_VENV="$BUILD_REMOTE_DIR/.hw-e2e-venv"
# Pinned: playwright 1.60.0 pins its WebKit build (the directory name below); check_vm.sh refuses
# any other.
PLAYWRIGHT_PIN="playwright==1.60.0"
WEBKIT_DIR_PIN="webkit-2287"
