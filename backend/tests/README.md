# Backend tests

  cd backend && python3 -m pytest -q tests

Needs fastapi + pytest (the system python3 has both; the /opt venv is not used or modified).
conftest.py forces HERDR_BIN to a fake client and HERDR_HOME to a scratch dir before the app is
imported, and fails the run if either points at the real herdr. Fakes live in tests/fakes/:
herdr_echo.py, herdr_exit.py, fake_herdr_api.py (scriptable unix-socket API server; fixture `fake_api`).
