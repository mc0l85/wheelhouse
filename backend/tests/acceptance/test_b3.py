"""B-3: /docs, /redoc, /openapi.json -> 404."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app, base_url="http://127.0.0.1")     # no `with`: the event hub stays off


def test_api_is_reachable(client):
    assert client.get("/api/health").status_code == 200       # the 404s below are not a host refusal


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_docs_disabled(client, path):
    assert client.get(path).status_code == 404, path
