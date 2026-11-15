#!/usr/bin/env python3
"""Local Playwright e2e for the herdr-web frontend (H0).

  python3 frontend/e2e/run_e2e.py <dist-dir> [--out results/<name>.json]

Serves <dist-dir> on 127.0.0.1:<random port>, mocks every /api/** route with
fixtures and the terminal WebSocket with page.route_web_socket (ping -> pong,
`ESC[?2004h` on open), records every byte the page sends, and runs scenarios.
Fully local: nothing touches herdr or the live site. Exit 1 if a scenario's text
is wrong. The JSON output holds the raw bytes (hex) per scenario for before/after diffs.
"""
import argparse, functools, http.server, json, sys, threading
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

PHRASES = ["t", "take", "take a", "take a look", "take a look at", "take a look at it."]

HEALTH = {"ok": True, "herdr_bin": "/fake/herdr", "session": "default", "server_running": True,
          "writes_enabled": True, "pane_count": 1}
PANE = {"pane_id": "w1:p1", "tab_id": "w1:t1", "workspace_id": "w1", "agent": "claude",
        "agent_status": "idle", "focused": True, "label": "fixture"}
TREE = {"workspaces": [{"workspace_id": "w1", "label": "fixture", "focused": True,
        "tabs": [{"tab_id": "w1:t1", "label": "1", "focused": True, "panes": [PANE]}]}]}
FIXTURES = {
    "/api/health": HEALTH,
    "/api/tree": TREE,
    "/api/panes": {"panes": [PANE]},
    "/api/workspaces": {"version": 1, "workspaces": []},
    "/api/push/key": {"public_key": "", "subscribers": 0},
}


def apply_bs(bs: bytes) -> str:
    """Final text after applying DEL as backspace."""
    out = []
    for ch in bs.decode("utf-8", "replace"):
        if ch == "\x7f":
            if out:
                out.pop()
        else:
            out.append(ch)
    return "".join(out)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(dist: Path):
    h = functools.partial(Quiet, directory=str(dist))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def api_route(route):
    path = urlparse(route.request.url).path
    body = FIXTURES.get(path)
    if body is None and path.startswith("/api/layout/"):
        body = {"panes": [{"pane_id": "w1:p1", "focused": True, "rect": {"x": 0, "y": 0, "width": 80, "height": 24}}]}
    if body is None and path.endswith("/read"):
        body = {"content": ""}
    if body is None:
        body = {}
    route.fulfill(status=200, body=json.dumps(body), content_type="application/json")


DICT_JS = """async ([phrases, mode]) => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const ta = document.querySelector('.xterm-helper-textarea'); ta.focus();
  ta.value = ''; const start = 0;
  for (const h of phrases) {
    const v = ta.value;
    ta.setSelectionRange(start, v.length);
    if (mode === 'insertText') {
      ta.dispatchEvent(new InputEvent('beforeinput', {inputType:'insertText', data:h, bubbles:true, cancelable:true}));
      ta.value = v.slice(0, start) + h; ta.setSelectionRange(ta.value.length, ta.value.length);
      ta.dispatchEvent(new InputEvent('input', {inputType:'insertText', data:h, bubbles:true}));
    } else {
      ta.dispatchEvent(new CompositionEvent('compositionstart', {data:'', bubbles:true}));
      ta.value = v.slice(0, start) + h; ta.setSelectionRange(ta.value.length, ta.value.length);
      ta.dispatchEvent(new CompositionEvent('compositionupdate', {data:h, bubbles:true}));
      ta.dispatchEvent(new InputEvent('input', {inputType:'insertCompositionText', data:h, isComposing:true, bubbles:true}));
      ta.dispatchEvent(new CompositionEvent('compositionend', {data:h, bubbles:true}));
    }
    await sleep(400);
  }
  await sleep(1800);
}"""

PREDICTIVE_JS = """async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const ta = document.querySelector('.xterm-helper-textarea'); ta.focus();
  for (const w of ["hi", "there"]) {
    ta.setSelectionRange(ta.value.length, ta.value.length);
    ta.dispatchEvent(new CompositionEvent('compositionstart', {data:'', bubbles:true}));
    let cur = '';
    const base = ta.value;
    for (const ch of w) { cur += ch; ta.value = base + cur;
      ta.dispatchEvent(new CompositionEvent('compositionupdate', {data:cur, bubbles:true}));
      ta.dispatchEvent(new InputEvent('input', {inputType:'insertCompositionText', data:cur, isComposing:true, bubbles:true}));
      await sleep(60); }
    ta.dispatchEvent(new CompositionEvent('compositionend', {data:cur, bubbles:true}));
    await sleep(400);
    ta.dispatchEvent(new InputEvent('beforeinput', {inputType:'insertText', data:' ', bubbles:true, cancelable:true}));
    ta.value += ' ';
    ta.dispatchEvent(new InputEvent('input', {inputType:'insertText', data:' ', bubbles:true}));
    await sleep(300);
  }
  await sleep(1500);
}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dist")
    ap.add_argument("--out")
    ap.add_argument("--rev", default="", help="git sha the dist was built from (recorded in the result)")
    a = ap.parse_args()
    dist = Path(a.dist).resolve()
    assert (dist / "index.html").exists(), f"no index.html in {dist}"
    srv = serve(dist)
    base = f"http://127.0.0.1:{srv.server_address[1]}/"
    sent = bytearray()
    texts, pings = [], []
    results = {"dist": dist.name, "rev": a.rev, "scenarios": {}}

    def term_ws(ws):
        def on_msg(m):
            if isinstance(m, (bytes, bytearray)):
                sent.extend(m)
                return
            try:
                msg = json.loads(m)
            except ValueError:
                return
            texts.append(msg)
            if msg.get("type") == "ping":
                pings.append(1)
                ws.send(json.dumps({"type": "pong"}))
        ws.on_message(on_msg)
        ws.send("\x1b[?2004h".encode())          # bracketed paste on, as herdr does

    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1000, "height": 700}, service_workers="block")
        pg = ctx.new_page()
        pg.route_web_socket("**/api/ws/terminal**", term_ws)
        pg.route_web_socket("**/api/ws/events**", lambda ws: None)   # accepted, silent
        pg.route("**/api/**", api_route)
        pg.goto(base, wait_until="domcontentloaded")
        pg.wait_for_selector(".xterm-helper-textarea", state="attached", timeout=20000)
        pg.wait_for_timeout(2500)

        def record(name, expect, gating=True):
            raw = bytes(sent)
            got = apply_bs(raw)
            results["scenarios"][name] = {"expected": expect, "text": got, "ok": got == expect, "gating": gating,
                                          "raw_hex": raw.hex(), "raw_repr": repr(raw.decode("utf-8", "replace"))}

        for mode in ("insertText", "composition"):
            sent.clear()
            pg.evaluate(DICT_JS, [PHRASES, mode])
            # The synthetic composition-mode dictation currently doubles on main too
            # (known, ported from dictsim.py): recorded, compared for identity, not gating.
            record(f"dictation-{mode}", "take a look at it.", gating=(mode == "insertText"))
        sent.clear()
        pg.evaluate(PREDICTIVE_JS)
        record("predictive", "hi there ")
        sent.clear()
        pg.locator(".xterm-helper-textarea").focus()
        pg.keyboard.type("hello world", delay=30)
        pg.wait_for_timeout(800)
        record("hardware", "hello world")
        results["ping_pong_seen"] = bool(pings)
        b.close()
    srv.shutdown()
    ok = all(s["ok"] or not s["gating"] for s in results["scenarios"].values())
    results["ok"] = ok
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(results, indent=1) + "\n")
    for n, s in results["scenarios"].items():
        print(f"{'PASS' if s['ok'] else ('FAIL' if s['gating'] else 'KNOWN-FAIL')} {n}: {s['text']!r}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
