# Backend tests

  cd backend && python3 -m pytest -q tests

Needs fastapi + pytest (the system python3 has both; the /opt venv is not used or modified).
conftest.py forces HERDR_BIN to a fake client and HERDR_HOME to a scratch dir before the app is
imported, and fails the run if either points at the real herdr. Fakes live in tests/fakes/:
herdr_echo.py, herdr_exit.py, fake_herdr_api.py (scriptable unix-socket API server; fixture `fake_api`).

Acceptance tests (P1, locked by prd/acceptance.json) live in tests/acceptance/, one file per ledger item:
  cd backend && python3 -m pytest -q tests/acceptance/test_a1.py
Close codes go through a real uvicorn server on 127.0.0.1 (random port); conftest brings `lo` up when the
check runs in an empty network namespace. Extra fakes: herdr_marker.py, herdr_flood.py, herdr_record.py.
