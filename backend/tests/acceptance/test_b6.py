"""B-6: the docs describe the system as built (replaces the owner's visual checklist, PRD 6.1).

Read as content, not as a word blacklist:
  API.md        the terminal WebSocket's close codes (1008 origin, 1012 client exited, 1013 terminal
                limit AND output backlog, 4500 herdr binary missing, 4503 herdr server not running) in a
                table, each refusal AFTER accept; the frame types (resize, ping -> pong, input; other JSON
                types dropped; binary frames forwarded verbatim); no live /docs (OpenAPI) claim.
  deployment    README / NETWORKING / ARCHITECTURE describe the two SYSTEM units that exist in deploy/
                (herdr.service, herdr-web.service) with Wants=/After= and no hard dependency; every
                systemctl / journalctl command names a unit that exists in deploy/ and is a system one
                (sudo, never --user); no retired unit names; every deploy/ path and install.sh flag the
                docs use exists; the env-file path is the one install.sh writes; the linger note is there.
  allowlist     the default allowlist in the docs and .env.example equals config.py's default; the note
                explains port-less matching, bracketed IPv6, `*.suffix` and `*`.
  token         no token-gate text anywhere (the token gate was removed).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DOCS = sorted(p for p in (ROOT / "docs").glob("*.md") if p.name != "PRD.md")
README = ROOT / "README.md"
ENV_EXAMPLE = ROOT / "backend" / ".env.example"
USER_DOCS = [README, *DOCS]


def text(p: Path) -> str:
    assert p.is_file(), f"missing {p.relative_to(ROOT)}"
    return p.read_text()


def section(md: str, heading_re: str) -> str:
    """The body of the first heading matching heading_re, up to the next heading of the same or higher level."""
    m = re.search(rf"^(#+)\s*{heading_re}.*$", md, re.M | re.I)
    assert m, f"no section matching {heading_re!r}"
    level = len(m.group(1))
    rest = md[m.end():]
    n = re.search(rf"^#{{1,{level}}}\s", rest, re.M)
    return rest[: n.start()] if n else rest


# ---- API.md ------------------------------------------------------------------------------------------

CLOSE_CODES = {
    "1008": [r"origin"],
    "1012": [r"exit"],
    "1013": [r"limit", r"backlog"],
    "4500": [r"binary", r"missing|not found"],
    "4503": [r"not running|down"],
}


def api_rows() -> list[str]:
    return [ln for ln in text(ROOT / "docs" / "API.md").splitlines() if ln.lstrip().startswith("|")]


@pytest.mark.parametrize("code", sorted(CLOSE_CODES))
def test_api_md_close_code_table(code):
    rows = [r for r in api_rows() if re.search(rf"(?<!\d){code}(?!\d)", r)]
    assert rows, f"docs/API.md has no table row for close code {code}"
    joined = " ".join(rows).lower()
    for want in CLOSE_CODES[code]:
        assert re.search(want, joined), f"close code {code}: its row does not say {want!r}: {rows}"


def test_api_md_refusals_after_accept_and_frames():
    api = text(ROOT / "docs" / "API.md")
    low = api.lower()
    assert not re.search(r"closed before accept|before (the )?accept\b", low), \
        "API.md still says a refusal closes before accept"
    assert re.search(r"after (the )?(websocket )?accept|accepts? (the (handshake|socket) )?first", low), \
        "API.md must say every refusal is a close AFTER accept (code + reason reach the browser)"
    ws = section(api, r"WebSockets")
    for frame in ("resize", "ping", "pong", "input"):
        assert f'"{frame}"' in ws or f"`{frame}`" in ws, f"WebSockets section does not describe the {frame} frame"
    assert re.search(r"(other|unknown|any other)[^.\n]{0,60}type[^.\n]{0,60}(dropped|ignored)", ws, re.I), \
        "WebSockets section must say text frames with another JSON type are dropped"
    assert re.search(r"binary[^.\n]{0,80}verbatim|verbatim[^.\n]{0,80}binary", ws, re.I), \
        "WebSockets section must say binary frames are forwarded verbatim"


def test_api_md_no_live_openapi_docs():
    for ln in text(ROOT / "docs" / "API.md").splitlines():
        if re.search(r"/docs\b|/redoc|openapi", ln, re.I):
            assert re.search(r"404|disabled|off|not served", ln, re.I), \
                f"API.md advertises interactive docs, which are disabled (B-3): {ln.strip()}"


# ---- deployment notes --------------------------------------------------------------------------------

UNITS = sorted(p.name for p in (ROOT / "deploy").glob("*.service"))


def test_deploy_has_the_two_units():
    assert {"herdr.service", "herdr-web.service"} <= set(UNITS), UNITS


def test_networking_describes_both_system_units():
    sec = section(text(ROOT / "docs" / "NETWORKING.md"), r"Running as system services")
    assert "/etc/systemd/system" in sec
    for u in ("herdr.service", "herdr-web.service"):
        assert u in sec, f"NETWORKING.md 'Running as system services' does not describe {u}"
    assert re.search(r"Wants=", sec) and re.search(r"After=", sec)
    assert re.search(r"no hard dependency|without taking down|independent", sec, re.I)
    assert "enable-linger" in sec, "the herdr unit needs the user to linger: the note is missing"
    # the herdr unit's keys as built (deploy/herdr.service)
    unit = text(ROOT / "deploy" / "herdr.service")
    for key in ("Restart=always", "KillMode=mixed", "OOMPolicy=continue", "TasksMax=infinity"):
        assert key in unit
        assert key in sec, f"NETWORKING.md does not mention {key} that herdr.service sets"


def test_architecture_describes_the_split():
    sec = section(text(ROOT / "docs" / "ARCHITECTURE.md"), r"Deployment units")
    for u in ("herdr.service", "herdr-web.service"):
        assert u in sec
    assert re.search(r"system unit", sec, re.I)
    assert re.search(r"restart\w*[^.]{0,80}(never|not|without)[^.]{0,80}(herdr server|panes)", sec, re.I), \
        "ARCHITECTURE.md must say restarting the web app leaves the herdr server and its panes alone"


def test_readme_install_names_the_system_services():
    sec = section(text(README), r"Install")
    assert "deploy/install.sh" in sec
    assert re.search(r"system (services|units)", sec, re.I)
    assert re.search(r"`herdr`", sec) and re.search(r"`herdr-web`", sec)


SYSTEMD_CMD = re.compile(r"(?P<sudo>sudo\s+)?(?P<tool>systemctl|journalctl)(?P<args>[^`\n]*)")


@pytest.mark.parametrize("doc", USER_DOCS, ids=lambda p: p.name)
def test_every_systemd_command_names_a_real_system_unit(doc):
    body = text(doc)
    assert not re.search(r"(systemctl|journalctl)\s+--user", body), f"{doc.name}: a --user unit command (units are system units)"
    assert not re.search(r"herdr-web-api", body), f"{doc.name}: names the retired herdr-web-api unit"
    for m in SYSTEMD_CMD.finditer(body):
        args = m.group("args").split()
        if m.group("tool") == "systemctl":
            if not args or args[0].startswith("-"):
                continue
            names = [a for a in args[1:] if not a.startswith("-")]
        else:
            names = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-u"]
        for n in names:
            n = n.strip("`.,;:)")
            unit = n if n.endswith(".service") else f"{n}.service"
            assert unit in UNITS, f"{doc.name}: `{m.group(0).strip()}` names {n}, not a unit in deploy/ ({UNITS})"
        if names:
            assert m.group("sudo"), f"{doc.name}: `{m.group(0).strip()}` on a system unit needs sudo"


def install_flags() -> set[str]:
    src = text(ROOT / "deploy" / "install.sh")
    return set(re.findall(r'"\$\{?1[^"]*"\s*=\s*"(--[a-z-]+)"', src)) | set(re.findall(r"^\s*(--[a-z-]+)\)", src, re.M))


@pytest.mark.parametrize("doc", USER_DOCS, ids=lambda p: p.name)
def test_install_steps_reference_what_exists(doc):
    body = text(doc)
    for path in set(re.findall(r"\bdeploy/[\w.-]+", body)):
        assert (ROOT / path).exists(), f"{doc.name}: references {path}, which does not exist"
    flags = install_flags()
    for flag in re.findall(r"install\.sh\s+(--[a-z-]+)", body):
        assert flag in flags, f"{doc.name}: `install.sh {flag}` is not a flag install.sh handles ({sorted(flags)})"


def test_env_file_path_matches_the_installer():
    src = text(ROOT / "deploy" / "install.sh")
    m = re.search(r'CONF_DIR="\$SERVICE_HOME/([^"]+)"', src)
    assert m, "install.sh CONF_DIR not found"
    conf = m.group(1)                                            # .config/herdr-web
    for doc in (README, ROOT / "docs" / "NETWORKING.md", ENV_EXAMPLE):
        body = text(doc)
        paths = re.findall(r"(?:~|/home/<user>|\$HOME)/([\w./-]+?)/env\b", body)
        assert paths, f"{doc.name}: does not say where the web unit's env file is"
        for pth in paths:
            assert pth == conf, f"{doc.name}: env file under {pth}, but install.sh writes {conf}/env"


# ---- allowlist ---------------------------------------------------------------------------------------

def default_allowlist() -> str:
    m = re.search(r'"HERDRWEB_ALLOWED_HOSTS",\s*"([^"]*)"', text(ROOT / "backend" / "app" / "config.py"))
    assert m, "default allowlist not found in config.py"
    return m.group(1)


def test_env_example_default_allowlist_matches_config():
    m = re.search(r"^HERDRWEB_ALLOWED_HOSTS=(.*)$", text(ENV_EXAMPLE), re.M)
    assert m and m.group(1).strip() == default_allowlist()


def test_allowlist_note_is_correct():
    net = text(ROOT / "docs" / "NETWORKING.md")
    default = default_allowlist()
    assert default in net, f"NETWORKING.md does not state the default allowlist ({default})"
    low = net.lower()
    assert re.search(r"without (its|the) port", low), "the note must say entries match without the port"
    assert "[::1]" in net, "the note must say bracketed IPv6 Hosts are understood"
    assert "*.example.com" in net or re.search(r"`\*\.[\w.-]+`", net), "the note must explain *.suffix"
    assert re.search(r"`\*`[^.\n]{0,60}(disables|any host|turns off)", net, re.I), "the note must say `*` disables the check"
    api = text(ROOT / "docs" / "API.md")
    assert re.search(r"HERDRWEB_ALLOWED_HOSTS[^.]{0,40}\*\*400\*\*|400[^.]{0,80}HERDRWEB_ALLOWED_HOSTS", api)


# ---- token gate --------------------------------------------------------------------------------------

TOKEN = re.compile(r"HERDRWEB_TOKEN|x-herdrweb-token|[?&]token=|token[- ]gate|(api|access|auth(entication)?) token|"
                   r"\btoken\b[^.\n]{0,40}\b(header|required|login|protect)", re.I)


@pytest.mark.parametrize("doc", [*USER_DOCS, ENV_EXAMPLE], ids=lambda p: p.name)
def test_no_token_gate_text(doc):
    hits = [ln.strip() for ln in text(doc).splitlines() if TOKEN.search(ln)]
    assert not hits, f"{doc.name}: token-gate text left: {hits}"
