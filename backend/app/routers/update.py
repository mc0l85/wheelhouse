"""/api/update — is there a newer Wheelhouse on GitHub than the build this browser runs?

The page sends the commit it was built from; this asks GitHub's compare API how far that commit is
behind the configured branch. One GitHub call per build per CACHE_S (errors: per ERROR_S), so the
unauthenticated rate limit (60/h per address) is never close. Off with HERDRWEB_UPDATE_CHECK=0.
"""
from __future__ import annotations

import re
import threading
import time

import requests
from fastapi import APIRouter, HTTPException

from ..config import settings

router = APIRouter(prefix="/api/update", tags=["update"])

CACHE_S = 6 * 3600
ERROR_S = 15 * 60
_MAX_CACHED = 32
_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_SLUG = re.compile(r"^[\w.-]+/[\w.-]+$")
_BRANCH = re.compile(r"^[\w./-]+$")

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}


# Paths whose change means a different running app (backend, frontend, how it is installed).
_APP_PREFIXES = ("backend/app/", "backend/requirements.txt", "frontend/src/", "frontend/public/",
                 "frontend/index.html", "frontend/package.json", "frontend/package-lock.json",
                 "frontend/vite.config.js", "deploy/")


def is_app_file(path: str) -> bool:
    return path.startswith(_APP_PREFIXES)


def fetch_compare(repo: str, base: str, head: str) -> tuple[int, dict]:
    """GitHub compare base...head → (HTTP status, JSON body). Patched out in tests."""
    r = requests.get(f"https://api.github.com/repos/{repo}/compare/{base}...{head}",
                     headers={"Accept": "application/vnd.github+json", "User-Agent": "wheelhouse-update-check"},
                     timeout=8)
    try:
        body = r.json()
    except ValueError:
        body = {}
    return r.status_code, body if isinstance(body, dict) else {}


def _check(sha: str) -> dict:
    repo, branch = settings.update_repo, settings.update_branch
    out = {"enabled": True, "repo": repo, "branch": branch, "running": sha[:7],
           "url": f"https://github.com/{repo}"}
    try:
        status, body = fetch_compare(repo, sha, branch)
    except requests.RequestException as e:
        return {**out, "status": "error", "detail": type(e).__name__}
    if status == 404:
        # The running commit is not on GitHub (a local, unpushed build) or the repo is gone.
        return {**out, "status": "unknown"}
    if status != 200:
        return {**out, "status": "error", "detail": f"github {status}"}
    gh = str(body.get("status", ""))
    behind = int(body.get("ahead_by") or 0)      # commits on the branch that the running build lacks
    commits = body.get("commits") or []
    latest = commits[-1] if commits and isinstance(commits[-1], dict) else {}
    files = [f.get("filename", "") for f in (body.get("files") or []) if isinstance(f, dict)]
    # Only a change to what actually runs counts: test, doc and ledger commits are not an update.
    # GitHub lists at most 300 files per compare; a full list may hide app files, so it counts.
    code = len(files) >= 300 or any(is_app_file(f) for f in files)
    res = {**out, "status": "behind" if behind > 0 and code else
           ("current" if behind > 0 or gh in ("identical", "behind") else gh or "unknown"),
           "behind_by": behind}
    if behind > 0 and not code:
        res["note"] = f"{behind} newer commit{'s' if behind != 1 else ''} on GitHub, none change the app"
    if res["status"] == "behind":
        msg = str((latest.get("commit") or {}).get("message", "")).split("\n", 1)[0]
        res.update(latest=str(latest.get("sha", ""))[:7], latest_message=msg[:120],
                   url=f"https://github.com/{repo}/compare/{sha[:12]}...{branch}")
    return res


@router.get("")
def update(sha: str = "") -> dict:
    if not settings.update_check:
        return {"enabled": False}
    sha = sha.strip().lower()
    if not _SHA.match(sha):
        raise HTTPException(400, "sha: 7-40 hex characters")
    if not _SLUG.match(settings.update_repo) or not _BRANCH.match(settings.update_branch):
        raise HTTPException(500, "HERDRWEB_UPDATE_REPO / _BRANCH is malformed")
    now = time.time()
    with _lock:
        hit = _cache.get(sha)
        if hit and hit[0] > now:
            return hit[1]
    res = _check(sha)
    ttl = ERROR_S if res["status"] == "error" else CACHE_S
    with _lock:
        if len(_cache) >= _MAX_CACHED:
            _cache.clear()
        _cache[sha] = (now + ttl, {**res, "checked_at": int(now)})
        return _cache[sha][1]
