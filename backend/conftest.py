"""Offline cutover fixtures must not depend on the cgroup the test runner happens to be in (a herdr pane
is inside the unit a rollback stops, which would make `--rollback` re-exec detached)."""
import os
import tempfile

_f = os.path.join(tempfile.mkdtemp(prefix="hwcg-"), "cgroup")
with open(_f, "w") as fh:
    fh.write("0::/user.slice/user-1000.slice/session-1.scope\n")
os.environ["HW_TEST_CGROUP_FILE"] = _f
