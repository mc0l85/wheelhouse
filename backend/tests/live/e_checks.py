#!/usr/bin/env python3
"""Live checks E-1..E-7 (P2, lane E): read-only probes of the cut-over host, run AFTER the cutover
(E-1 also before it). Stdlib only; run with the host's python3 as the service user:

  HW_HOST_ENV=<host-env file> python3 backend/tests/live/e_checks.py E-2

HW_HOST_ENV is the same host-values file deploy/cutover.sh reads (kept outside the repo). Every check
first refuses to run anywhere but the host it names (HOST_NAME == this machine's host name).

READ-ONLY: every check only reads /proc, /sys/fs/cgroup, files, `herdr pane list` / `pane
process-info` / `status server --json`, `systemctl show` and HTTP GETs -- EXCEPT E-3, which restarts
the web unit once (its whole point). E-3 refuses (exit 3, nothing touched) unless HW_E3_RESTART_OK=1
AND E-2's conditions hold right now (the herdr server is in HERDR_UNIT, none is in the web unit):
restarting the web unit while it still holds the server would kill every pane.
Exit 0 pass, 1 fail, 2 environment missing, 3 E-3 refused.
"""
from __future__ import annotations

import glob
import hashlib
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


class Fail(Exception):
    pass


class Refuse(Exception):
    pass


def host_env() -> dict:
    path = os.environ.get("HW_HOST_ENV")
    if not path or not os.path.isfile(path):
        print("e_checks: HW_HOST_ENV must name the host-values file", file=sys.stderr)
        sys.exit(2)
    v = {}
    for ln in Path(path).read_text().splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, x = ln.split("=", 1)
            v[k.strip()] = x.strip()
    v.setdefault("HERDR_HOME", os.path.join(v.get("SERVICE_HOME", ""), ".config", "herdr"))
    return v


def need(cond, msg):
    if not cond:
        raise Fail(msg)


def herdr(v, *args) -> dict:
    r = subprocess.run([v["HERDR_BIN"], *args], capture_output=True, text=True, timeout=30,
                       env=dict(os.environ, HOME=v["SERVICE_HOME"]))
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise Fail(f"herdr {' '.join(args)}: rc={r.returncode} {r.stdout[:200]} {r.stderr[:200]}")


def panes(v) -> dict:
    out = herdr(v, "pane", "list")
    need("result" in out, f"pane list failed: {out}")
    return {p["pane_id"]: p for p in out["result"]["panes"]}


def argv_of(pid: int) -> list[str]:
    try:
        return [a.decode(errors="replace") for a in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if a]
    except OSError:
        return []


def server_pids(v) -> list[int]:
    uid = os.stat(v["HERDR_HOME"]).st_uid
    out = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            if os.stat(d).st_uid != uid:
                continue
        except OSError:
            continue
        a = argv_of(int(d.name))
        if any(a[i] == v["HERDR_BIN"] and a[i + 1] == "server" for i in range(min(2, len(a) - 1))):
            out.append(int(d.name))
    return sorted(out)


def cgroup_of(pid: int) -> str:
    return Path(f"/proc/{pid}/cgroup").read_text().strip().split("::", 1)[-1]


def main_pid(unit: str) -> int:
    r = subprocess.run(["systemctl", "show", "--property=MainPID", "--value", unit], capture_output=True, text=True)
    return int(r.stdout.strip() or 0)


def unit_procs(unit: str) -> list[int]:
    p = Path(f"/sys/fs/cgroup/system.slice/{unit}/cgroup.procs")
    return [int(x) for x in p.read_text().split()] if p.exists() else []


def latest_run(v) -> Path:
    link = Path(v["STATE_DIR"]) / "latest"
    need(link.is_symlink(), f"no cutover run recorded ({link} missing)")
    return link.resolve()


def web_addr(v) -> tuple[str, int]:
    host = port = None
    for ln in Path(v["CONF_DIR"], "env").read_text().splitlines():
        if ln.startswith("HERDRWEB_HOST="):
            host = ln.split("=", 1)[1].strip()
        elif ln.startswith("HERDRWEB_PORT="):
            port = int(ln.split("=", 1)[1].strip())
    need(host and port, "CONF_DIR/env has no HERDRWEB_HOST / HERDRWEB_PORT")
    return host, port


# ---------------------------------------------------------------- checks

def e2_state(v) -> dict:
    ss = server_pids(v)
    need(len(ss) == 1, f"want exactly one herdr server, found {ss}")
    pid = ss[0]
    cg = cgroup_of(pid)
    need(cg.endswith("/" + v["HERDR_UNIT"]), f"herdr server {pid} is in {cg}, not {v['HERDR_UNIT']}")
    need(main_pid(v["HERDR_UNIT"]) == pid, f"{v['HERDR_UNIT']} MainPID is not the server {pid}")
    st = herdr(v, "status", "server", "--json")
    need(st.get("running") is True, f"status server: {st}")
    need(os.path.realpath(st.get("socket", "")) == os.path.realpath(os.path.join(v["HERDR_HOME"], "herdr.sock")),
         f"status server socket {st.get('socket')} is not HERDR_HOME's")
    web_servers = [p for p in unit_procs(v["WEB_UNIT"]) if p in ss]
    need(not web_servers, f"a herdr server runs in {v['WEB_UNIT']}: {web_servers}")
    return {"server_pid": pid, "cgroup": cg}


def _newest_result(v):
    """The newest run's result.json, or None while no cutover has been recorded."""
    link = Path(v["STATE_DIR"]) / "latest"
    if not link.is_symlink():
        return None
    try:
        return link.resolve(), json.loads((link.resolve() / "result.json").read_text())
    except (OSError, ValueError):
        return None


def e1_after_cutover(v, run: Path) -> dict:
    """After the cutover the dry-run preflight cannot pass (it wants the legacy unit present, the new ones
    absent), so judge the completed run: its manifest against the live panes."""
    m = json.loads((run / "manifest.json").read_text())
    rows = {x["pane_id"]: x for x in m["panes"]}
    live = panes(v)
    need(set(rows) == set(live), f"manifest panes {sorted(rows)} != live panes {sorted(live)}")
    for pid, row in rows.items():
        if row["agent"]:
            need(live[pid].get("agent") == row["agent"], f"{pid}: agent {row['agent']} is not live ({live[pid].get('agent')})")
    return {"panes": len(rows), "agents": sum(1 for x in rows.values() if x["agent"]), "after_cutover": True}


def e1(v):
    newest = _newest_result(v)
    if newest and newest[1].get("outcome") == "done":
        return e1_after_cutover(v, newest[0])
    # the dry run is read-only only once E-F1 proved it offline: never run an unproven one on the live host
    led = json.loads((REPO / "prd" / "acceptance.json").read_text())
    ef1 = next((i for i in led["items"] if i["id"] == "E-F1"), {})
    need(ef1.get("status") == "done", "E-F1 (dry run changes nothing) is not verified: refusing to dry-run live")
    r = subprocess.run([str(REPO / "deploy" / "cutover.sh"), "--dry-run", "--host-env", os.environ["HW_HOST_ENV"]],
                       capture_output=True, text=True, timeout=120)
    need(r.returncode == 0, f"dry run rc={r.returncode}: {r.stderr[-500:]}")
    m = json.loads(r.stdout)["manifest"]
    rows = {x["pane_id"]: x for x in m["panes"]}
    live = panes(v)
    need(set(rows) == set(live), f"manifest panes {sorted(rows)} != live panes {sorted(live)}")
    for pid, p in live.items():
        row = rows[pid]
        if p.get("agent"):
            need(row["agent"] == p["agent"], f"{pid}: agent {row['agent']} != {p['agent']}")
            need(row["session"] and row["session"]["value"] == (p.get("agent_session") or {}).get("value"),
                 f"{pid}: session ref missing or different")
            need(row["action"] in ("resume", "fresh") and row["relaunch"], f"{pid}: no planned relaunch")
        else:
            need(row["action"] == "none", f"{pid}: a shell pane has action {row['action']}")
    return {"panes": len(rows), "agents": sum(1 for x in rows.values() if x["agent"])}


def e2(v):
    return e2_state(v)


def e3(v):
    if os.environ.get("HW_E3_RESTART_OK") != "1":
        raise Refuse("E-3 restarts the web unit: set HW_E3_RESTART_OK=1 to allow it (after the cutover only)")
    try:
        before = e2_state(v)
    except Fail as e:
        raise Refuse(f"E-2 does not hold, refusing to restart the web unit: {e}")
    ps = panes(v)
    children = {}
    for pid in ps:
        info = herdr(v, "pane", "process-info", "--pane", pid)["result"]["process_info"]
        children[pid] = sorted([info.get("shell_pid")] + [f["pid"] for f in info.get("foreground_processes", [])])
    r = subprocess.run(["sudo", "-n", "systemctl", "restart", v["WEB_UNIT"]], capture_output=True, text=True)
    need(r.returncode == 0, f"restart {v['WEB_UNIT']} failed: {r.stderr.strip()}")
    host, port = web_addr(v)
    end = time.time() + 30
    while time.time() < end:
        try:
            socket.create_connection((host, port), timeout=1).close()
            break
        except OSError:
            time.sleep(0.5)
    else:
        raise Fail("the web unit did not come back within 30 s")
    after = e2_state(v)
    need(after["server_pid"] == before["server_pid"], f"server PID changed {before['server_pid']} -> {after['server_pid']}")
    ps2 = panes(v)
    need(set(ps2) == set(ps), f"pane set changed: {sorted(set(ps) ^ set(ps2))}")
    for pid, kids in children.items():
        info = herdr(v, "pane", "process-info", "--pane", pid)["result"]["process_info"]
        now = sorted([info.get("shell_pid")] + [f["pid"] for f in info.get("foreground_processes", [])])
        need(now == kids, f"{pid}: pane processes changed {kids} -> {now}")
    for p in unit_procs(v["WEB_UNIT"]):
        a = argv_of(p)
        ok = any("uvicorn" in x for x in a[:3]) or (v["HERDR_BIN"] in a[:2] and "--session" in a)
        need(ok or not a, f"unexpected process in {v['WEB_UNIT']}: {p} {a}")
    return {"server_pid": after["server_pid"], "panes": len(ps2)}


def e4(v):
    run = latest_run(v)
    m = json.loads((run / "manifest.json").read_text())
    started = m["started_at"]
    live = panes(v)
    for row in m["panes"]:
        if not row["agent"]:
            continue
        p = live.get(row["pane_id"])
        need(p and p.get("agent") == row["agent"], f"{row['pane_id']}: agent {row['agent']} did not come back")
        if row["action"] != "resume":
            continue
        val = row["session"]["value"]
        # herdr's own label drifts when a child claude runs in the pane, so the label is only a warning;
        # the run's recorded session_match, the foreground argv and the transcript are the checks.
        res = next((a for a in (_newest_result(v) or (None, {}))[1].get("agents", []) if a.get("pane_id") == row["pane_id"]), None)
        need(res and res.get("session_match") is True, f"{row['pane_id']}: result.json session_match is not true")
        if (p.get("agent_session") or {}).get("value") != val:
            print(f"WARNING: {row['pane_id']}: herdr's session label differs from the manifest's {val}", flush=True)
        info = herdr(v, "pane", "process-info", "--pane", row["pane_id"])["result"]["process_info"]
        need(any(val in " ".join(argv_of(f["pid"])) for f in info.get("foreground_processes", [])) or
             any(val in " ".join(f.get("argv", [])) for f in info.get("foreground_processes", [])),
             f"{row['pane_id']}: /proc cmdline lacks the session id")
        if row["agent"] == "claude":
            files = glob.glob(os.path.join(v["SERVICE_HOME"], ".claude", "projects", "*", f"{val}.jsonl"))
            need(files, f"{row['pane_id']}: transcript {val}.jsonl not found")
            got = False
            for ln in Path(files[0]).read_text().splitlines():
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                msg = e.get("message") or {}
                content = msg.get("content")
                text = content if isinstance(content, str) else " ".join(
                    c.get("text", "") for c in content or [] if isinstance(c, dict))
                if e.get("type") == "user" and text.strip() == "resume" and _later(e.get("timestamp", ""), started):
                    got = True
                    break
            need(got, f"{row['pane_id']}: transcript has no user message `resume` after {started}")
    return {"agents": sum(1 for r in m["panes"] if r["agent"])}


def _later(ts: str, ref: str) -> bool:
    from datetime import datetime
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")) > datetime.fromisoformat(ref)
    except ValueError:
        return False


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def tree(root: Path) -> dict:
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts}


def e5(v):
    stage, app = Path(v["STAGE_DIR"]) / "backend", Path(v["APP_DIR"]) / "backend"
    need(tree(app / "app") == tree(stage / "app"), "deployed backend/app differs from the staged tree")
    need(sha(app / "requirements.txt") == sha(stage / "requirements.txt"), "requirements.txt differs")
    rev = (Path(v["STAGE_DIR"]) / "REVISION").read_text().strip()
    for rel, digest in tree(stage / "app").items():
        blob = subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:backend/app/{rel}"], capture_output=True)
        need(blob.returncode == 0 and hashlib.sha256(blob.stdout).hexdigest() == digest,
             f"staged backend/app/{rel} is not the repo's at {rev[:12]}")
    host, port = web_addr(v)
    c = http.client.HTTPConnection(host, port, timeout=10)
    c.request("GET", "/", headers={"Host": f"{host}:{port}"})
    r = c.getresponse()
    index = r.read()
    need(r.status == 200, f"GET / -> {r.status}")
    need(hashlib.sha256(index).hexdigest() == sha(stage / "static" / "index.html"), "served index.html is not the build output")
    assets = sorted(set(re.findall(rb'(?:src|href)="(/assets/[^"]+)"', index)))
    need(assets, "index.html references no /assets/")
    for a in assets:
        c.request("GET", a.decode(), headers={"Host": f"{host}:{port}"})
        r = c.getresponse()
        body = r.read()
        need(r.status == 200 and hashlib.sha256(body).hexdigest() == sha(stage / "static" / a.decode().lstrip("/")),
             f"served {a.decode()} is not the build output")
    return {"revision": rev[:12], "assets": len(assets)}


def e6(v):
    res = json.loads((latest_run(v) / "result.json").read_text())
    g = res.get("guard") or {}
    need(g.get("code") == 4503 and g.get("server_appeared") is False,
         f"the cutover did not prove the guard in its window: {g}")
    return {"guard": g}


def e7(v):
    script = Path(v["UPDATE_SCRIPT"])
    need(script.is_file(), f"{script} missing")
    need(sha(script) == sha(REPO / "deploy" / "herdr-update.sh"), "the installed nightly script is not the repo's")
    code = [ln for ln in script.read_text().splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    need(not [ln for ln in code if "--handoff" in ln], "the nightly script still calls --handoff")
    unit = Path(v["SERVICE_HOME"], ".config", "systemd", "user", "herdr-update.service")
    need(unit.is_file(), f"{unit} missing")
    execs = [ln.split("=", 1)[1].strip().replace("%h", v["SERVICE_HOME"])
             for ln in unit.read_text().splitlines() if ln.startswith("ExecStart=")]
    need(execs and os.path.realpath(execs[-1].split()[0]) == os.path.realpath(script),
         f"herdr-update.service runs {execs}, not {script}")
    return {"script": str(script)}


CHECKS = {"E-1": e1, "E-2": e2, "E-3": e3, "E-4": e4, "E-5": e5, "E-6": e6, "E-7": e7}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in CHECKS:
        print(f"usage: {sys.argv[0]} {{{','.join(CHECKS)}}}", file=sys.stderr)
        return 2
    v = host_env()
    want = v.get("HOST_NAME")
    if not want or socket.gethostname() != want:
        print(f"FAIL {sys.argv[1]}: this is {socket.gethostname()}, the check is for HOST_NAME={want}", file=sys.stderr)
        return 1
    try:
        detail = CHECKS[sys.argv[1]](v)
    except Refuse as e:
        print(f"REFUSED {sys.argv[1]}: {e}", file=sys.stderr)
        return 3
    except (Fail, OSError, KeyError, ValueError) as e:
        print(f"FAIL {sys.argv[1]}: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"PASS {sys.argv[1]}: {json.dumps(detail)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
