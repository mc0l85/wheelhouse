"""/api/update: GitHub compare → behind / current / unknown / error, cached, and off by env."""
from __future__ import annotations

import pytest
import requests
from fastapi.testclient import TestClient

from app import main
from app.routers import update

SHA = "6965e7a989c230b4451181e1dad8aabc660c9704"


@pytest.fixture
def gh(monkeypatch):
    calls: list[tuple[str, str, str]] = []
    reply: dict = {"v": (200, {})}

    def fake(repo, base, head):
        calls.append((repo, base, head))
        r = reply["v"]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(update, "fetch_compare", fake)
    update._cache.clear()
    yield calls, reply
    update._cache.clear()


def client() -> TestClient:
    return TestClient(main.app, base_url="http://localhost")


def test_behind_reports_count_latest_and_compare_url(gh):
    calls, reply = gh
    reply["v"] = (200, {"status": "ahead", "ahead_by": 3, "behind_by": 0, "commits": [
        {"sha": "a" * 40, "commit": {"message": "one"}},
        {"sha": "b" * 40, "commit": {"message": "newest change\n\nbody"}}],
        "files": [{"filename": "prd/acceptance.json"}, {"filename": "frontend/src/input/composer.ts"}]})
    r = client().get(f"/api/update?sha={SHA}").json()
    assert r["status"] == "behind" and r["behind_by"] == 3
    assert r["latest"] == "bbbbbbb" and r["latest_message"] == "newest change"
    assert r["url"] == "https://github.com/mc0l85/wheelhouse/compare/6965e7a989c2...main"
    assert calls == [("mc0l85/wheelhouse", SHA, "main")]


@pytest.mark.parametrize("gh_status", ["identical", "behind"])
def test_identical_or_newer_is_current(gh, gh_status):
    _, reply = gh
    reply["v"] = (200, {"status": gh_status, "ahead_by": 0, "behind_by": 2 if gh_status == "behind" else 0})
    assert client().get(f"/api/update?sha={SHA}").json()["status"] == "current"


def test_housekeeping_only_commits_are_not_an_update(gh):
    _, reply = gh
    reply["v"] = (200, {"status": "ahead", "ahead_by": 2, "commits": [{"sha": "c" * 40, "commit": {"message": "ledger"}}],
                        "files": [{"filename": "prd/acceptance.json"}, {"filename": "backend/tests/test_x.py"},
                                  {"filename": "docs/API.md"}, {"filename": "frontend/test/a.test.ts"}]})
    r = client().get(f"/api/update?sha={SHA}").json()
    assert r["status"] == "current" and "none change the app" in r["note"]


def test_truncated_file_list_counts_as_update(gh):
    _, reply = gh
    reply["v"] = (200, {"status": "ahead", "ahead_by": 40, "commits": [{"sha": "d" * 40, "commit": {"message": "big"}}],
                        "files": [{"filename": f"docs/f{i}.md"} for i in range(300)]})
    assert client().get(f"/api/update?sha={SHA}").json()["status"] == "behind"


def test_unpushed_build_is_unknown(gh):
    _, reply = gh
    reply["v"] = (404, {"message": "Not Found"})
    assert client().get(f"/api/update?sha={SHA}").json()["status"] == "unknown"


def test_network_error_is_error_not_500(gh):
    _, reply = gh
    reply["v"] = requests.ConnectionError("down")
    r = client().get(f"/api/update?sha={SHA}")
    assert r.status_code == 200 and r.json()["status"] == "error"


def test_one_github_call_per_build_while_cached(gh):
    calls, reply = gh
    reply["v"] = (200, {"status": "identical", "ahead_by": 0})
    c = client()
    for _ in range(5):
        c.get(f"/api/update?sha={SHA}")
    assert len(calls) == 1


def test_bad_sha_is_refused_without_a_call(gh):
    calls, _ = gh
    for bad in ["", "xyz", "../../etc", "a" * 41, "abc"]:
        assert client().get("/api/update", params={"sha": bad}).status_code == 400
    assert calls == []


def test_server_switch_off_makes_no_call(gh):
    calls, _ = gh
    object.__setattr__(update.settings, "update_check", False)   # Settings is a frozen dataclass
    try:
        assert client().get(f"/api/update?sha={SHA}").json() == {"enabled": False}
    finally:
        object.__setattr__(update.settings, "update_check", True)
    assert calls == []
