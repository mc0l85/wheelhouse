# Frontend e2e (Python Playwright, fully local)

Setup (once): `pip install -r frontend/e2e/requirements.txt && python3 -m playwright install chromium`

Build on a separate build VM (not on the dev host). Set BUILD_SSH_JUMP, BUILD_SSH_PORT,
BUILD_SSH_TARGET, BUILD_NODE_DIR and BUILD_REMOTE_DIR (no defaults; see the script header), then:
  frontend/e2e/build_remote.sh <frontend-src> <dist-out> <tag> [VITE_INPUT_TRACE=1]
Run:
  python3 frontend/e2e/run_e2e.py <dist-out> --rev <git-sha> --out frontend/e2e/results/<name>.json
Compare two runs byte-for-byte:
  python3 frontend/e2e/compare.py a.json b.json
`dictation-composition` is a known-failing synthetic scenario on main@34c297f (recorded, compared, not gating).
Dev input recorder: build with VITE_INPUT_TRACE=1 and run the backend with HERDRWEB_DEBUG_INPUT=1.
