"""B-4: backend/requirements.txt is the full pinned freeze of the running venv:
every requirement line is exactly `name==version`, and the set equals the freeze below."""
from __future__ import annotations

import re
from pathlib import Path

REQ = Path(__file__).resolve().parents[2] / "requirements.txt"

# `pip freeze` of the deployed venv, 2026-11-15.
FREEZE = """
aiohappyeyeballs==2.7.1
aiohttp==3.14.5
aiosignal==1.4.0
annotated-doc==0.0.4
annotated-types==0.7.0
anyio==4.13.0
attrs==26.1.0
certifi==2026.10.23
cffi==2.1.1
charset-normalizer==3.5.2
click==8.4.1
cryptography==50.0.2
fastapi==0.136.3
frozenlist==1.8.0
h11==0.16.0
http_ece==1.2.1
httptools==0.8.0
idna==3.18
multidict==7.2.1
propcache==0.5.4
py-vapid==1.9.6
pycparser==3.12
pydantic==2.13.4
pydantic_core==2.46.4
python-dotenv==1.2.2
pywebpush==2.0.3
PyYAML==6.0.3
requests==2.34.2
six==1.17.0
starlette==1.2.1
typing-inspection==0.4.2
typing_extensions==4.15.0
urllib3==2.8.0
uvicorn==0.49.0
uvloop==0.22.1
watchfiles==1.2.0
websockets==16.0
yarl==1.25.1
"""

MUST = {"fastapi": "0.136.3", "starlette": "1.2.1", "uvicorn": "0.49.0", "websockets": "16.0",
        "uvloop": "0.22.1", "httptools": "0.8.0", "h11": "0.16.0", "anyio": "4.13.0",
        "pydantic": "2.13.4", "pydantic-core": "2.46.4", "pywebpush": "2.0.3", "cryptography": "50.0.2"}

LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.+!_-]*)$")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = LINE.match(line)
        assert m, f"not an exact `name==version` pin: {raw!r}"
        assert norm(m.group(1)) not in out, f"duplicate requirement: {raw!r}"
        out[norm(m.group(1))] = m.group(2)
    return out


def test_every_line_is_an_exact_pin():
    parse(REQ.read_text())


def test_requirements_equal_the_freeze():
    got, want = parse(REQ.read_text()), parse(FREEZE)
    missing = {k: v for k, v in want.items() if got.get(k) != v}
    extra = sorted(set(got) - set(want))
    assert not missing and not extra, f"missing/wrong: {missing}; not in the freeze: {extra}"


def test_named_packages_pinned():
    got = parse(REQ.read_text())
    for name, ver in MUST.items():
        assert got.get(name) == ver, f"{name} must be pinned =={ver}, got {got.get(name)}"
