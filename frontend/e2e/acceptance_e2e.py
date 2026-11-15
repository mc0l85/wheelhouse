#!/usr/bin/env python3
"""Acceptance e2e scenarios (P1 ledger items C-2, C-15, D-6) on top of the H0 harness.

  python3 frontend/e2e/acceptance_e2e.py <dist-dir> --items C-2,C-15,D-6 [--out result.json]

Fully local, like run_e2e.py: <dist-dir> is served on 127.0.0.1:<random port>, every /api route
is mocked, the terminal WebSocket is a page.route_web_socket mock (ping -> pong, bracketed paste
on) that records every byte the page sends. A WebSocket that is NOT mocked reaches the local
static server, which holds the upgrade forever (a handshake that never opens).

Pinned page facts (beyond today's DOM): the modifier indicator is `.mod-ind` (shown while Ctrl/Alt
is armed); the connection message from socketPolicy (which contains the close reason) is visible
text on the page; toasts are `.toast` (`.toast.bad` for warnings); the Mic button is `button.mic-btn`
and delivers through POST /api/panes/<id>/send-text and .../send-keys (a fake recognizer is injected).
Exit 1 if any selected scenario fails.
"""
import argparse, base64, functools, http.server, json, re, sys, threading, time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_e2e import DICT_JS, FIXTURES, PHRASES, PREDICTIVE_JS, apply_bs  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

FE = Path(__file__).resolve().parents[1]
PASTE = "e2e\x1b[201~\x07X\u009bY\x7fZ\t1"
PASTE_CLEAN = "e2e[201~XYZ\t1"
BREAKOUT = "safe\x1b[201~rm -rf ~\r\x1b[200~"
BREAKOUT_CLEAN = "safe[201~rm -rf ~\r[200~"
IMG_PATH = "/up/e2e\x1b[201~img.png"
IMG_PATH_CLEAN = "/up/e2e[201~img.png"
BP0, BP1 = b"\x1b[200~", b"\x1b[201~"
# a dictation hypothesis that rewrites an earlier word ("lock" -> "look"), not just extends
REWRITE = ["take", "take a", "take a lock", "take a look at it."]

ITEMS = {
    "C-2": ["paste-native", "paste-ctrlv", "paste-keyboard", "paste-controls", "paste-image", "osc52-cap"],
    "C-15": ["dictation-insertText", "dictation-composition", "dictation-rewrite-insertText",
             "dictation-rewrite-composition", "predictive", "hardware", "paste-breakout",
             "ctrl-composition", "ctrl-key", "tray-clear", "reset-mid-composition"],
    "D-6": ["close-message", "never-open", "mic-blocked", "mic-enter"],
}


def osc52_max_bytes() -> int:
    src = (FE / "src/input/sanitize.ts").read_text()
    m = re.search(r"OSC52_MAX_BYTES\s*=\s*([0-9\s<*+()x_]+);", src)
    assert m, "OSC52_MAX_BYTES not found in src/input/sanitize.ts"
    expr = m.group(1).replace("_", "")
    assert re.fullmatch(r"[0-9\s<*+()x]+", expr)
    return int(eval(expr, {"__builtins__": {}}))


INIT_JS = r"""
(() => {
  window.__clip = []; window.__clipText = ""; window.__seen = []; window.__toasts = [];
  const clip = {
    writeText: async (t) => { window.__clip.push(t); },
    readText: async () => window.__clipText,
    read: async () => [{ types: ["text/plain"], getType: async () => new Blob([window.__clipText], { type: "text/plain" }) }],
  };
  Object.defineProperty(navigator, "clipboard", { value: clip, configurable: true });
  // Mic: a fake recognizer that hears "e2e dictated words"; a granted, empty microphone.
  class FakeRec {
    start() { setTimeout(() => { this.onaudiostart && this.onaudiostart();
      const r = [{ transcript: "e2e dictated words" }]; r.isFinal = true;
      this.onresult && this.onresult({ resultIndex: 0, results: [r] }); }, 100); }
    stop() { setTimeout(() => this.onend && this.onend(), 50); }
    abort() {}
  }
  window.SpeechRecognition = FakeRec; window.webkitSpeechRecognition = FakeRec;
  if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = async () => ({ getTracks: () => [] });
  const exec = document.execCommand.bind(document);
  document.execCommand = (cmd, ...a) => {
    if (cmd === "copy") { const s = document.getSelection(); window.__clip.push(document.activeElement?.value ?? String(s)); return true; }
    return exec(cmd, ...a);
  };
  let lastToast = "";
  const scan = () => {
    const t = document.body ? document.body.innerText : "";
    for (const m of t.match(/e2e-marker-\d+/g) || []) if (!window.__seen.includes(m)) window.__seen.push(m);
    const el = document.querySelector(".toast");
    const now = el ? el.className + "|" + el.textContent : "";
    if (now && now !== lastToast) window.__toasts.push(now);
    lastToast = now;
  };
  new MutationObserver(scan).observe(document, { subtree: true, childList: true, characterData: true, attributes: true });
})();
"""

PASTE_EVENT_JS = """async ([text, withImage]) => {
  const ta = document.querySelector('.xterm-helper-textarea'); ta.focus();
  const dt = new DataTransfer();
  if (text !== null) dt.setData('text/plain', text);
  if (withImage) dt.items.add(new File([new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10])], 'shot.png', { type: 'image/png' }));
  ta.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
}"""

CTRL_V_JS = """() => {
  const ta = document.querySelector('.xterm-helper-textarea'); ta.focus();
  ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'v', code: 'KeyV', keyCode: 86, ctrlKey: true, bubbles: true, cancelable: true }));
}"""

COMPOSE_JS = """async (word) => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const ta = document.querySelector('.xterm-helper-textarea'); ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
  const base = ta.value;
  ta.dispatchEvent(new CompositionEvent('compositionstart', { data: '', bubbles: true }));
  let cur = '';
  for (const ch of word) {
    ta.dispatchEvent(new CompositionEvent('compositionupdate', { data: cur + ch, bubbles: true }));
    cur += ch; ta.value = base + cur; ta.setSelectionRange(ta.value.length, ta.value.length);
    ta.dispatchEvent(new InputEvent('input', { inputType: 'insertCompositionText', data: cur, isComposing: true, bubbles: true }));
    await sleep(60);
  }
  ta.dispatchEvent(new CompositionEvent('compositionend', { data: cur, bubbles: true }));
  await sleep(600);
}"""


COMP_STEP_JS = """([kind, data]) => {
  const ta = document.querySelector('.xterm-helper-textarea'); ta.focus();
  if (kind === 'start') { ta.value = ''; ta.dispatchEvent(new CompositionEvent('compositionstart', { data: '', bubbles: true })); return; }
  if (kind === 'update') {
    ta.dispatchEvent(new CompositionEvent('compositionupdate', { data, bubbles: true }));
    ta.value = data; ta.setSelectionRange(data.length, data.length);
    ta.dispatchEvent(new InputEvent('input', { inputType: 'insertCompositionText', data, isComposing: true, bubbles: true }));
    return;
  }
  ta.dispatchEvent(new CompositionEvent('compositionend', { data, bubbles: true }));
}"""


class Holder(http.server.SimpleHTTPRequestHandler):
    """Static files; an un-mocked WebSocket upgrade is counted and held (never answered)."""
    ws_hits: dict = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/ws/"):
            Holder.ws_hits[path] = Holder.ws_hits.get(path, 0) + 1
            time.sleep(60)
            return
        super().do_GET()


def serve(dist: Path):
    h = functools.partial(Holder, directory=str(dist))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def make_api_route(status="idle", log=None):
    """Mocked /api with the fixture pane in agent status `status`; requests appended to `log`."""
    pane = {**FIXTURES["/api/panes"]["panes"][0], "agent_status": status}
    tree = json.loads(json.dumps(FIXTURES["/api/tree"]))
    tree["workspaces"][0]["tabs"][0]["panes"] = [pane]
    over = {"/api/panes": {"panes": [pane]}, "/api/tree": tree, "/api/panes/w1:p1": pane, "/api/panes/w1%3Ap1": pane}

    def route(r):
        if log is not None:
            log.append((r.request.method, urlparse(r.request.url).path))
        path = urlparse(r.request.url).path
        if path in over:
            r.fulfill(status=200, body=json.dumps(over[path]), content_type="application/json")
        else:
            api_route(r)
    return route


def api_route(route):
    path = urlparse(route.request.url).path
    if path == "/api/upload/image":
        body = {"path": IMG_PATH, "bytes": 2048, "type": "image/png"}
    else:
        body = FIXTURES.get(path)
        if body is None and path.startswith("/api/layout/"):
            body = {"panes": [{"pane_id": "w1:p1", "focused": True, "rect": {"x": 0, "y": 0, "width": 80, "height": 24}}]}
        if body is None and path.endswith("/read"):
            body = {"content": ""}
        if body is None:
            body = {}
    route.fulfill(status=200, body=json.dumps(body), content_type="application/json")


class Term:
    """The terminal WebSocket mock for one page."""
    def __init__(self, close_seq=()):
        self.sent = bytearray()
        self.close_seq = list(close_seq)
        self.ws = None
        self.connections = 0
        self.to_close = []          # closed from the main loop: a sync-API call inside the handler deadlocks

    def flush_closes(self):
        while self.to_close:
            ws, code, reason = self.to_close.pop(0)
            ws.close(code=code, reason=reason)

    def handler(self, ws):
        self.connections += 1
        if self.close_seq:
            code, reason = self.close_seq.pop(0)
            self.to_close.append((ws, code, reason))
            return
        self.ws = ws

        def on_msg(m):
            if isinstance(m, (bytes, bytearray)):
                self.sent.extend(m)
                return
            try:
                msg = json.loads(m)
            except ValueError:
                return
            if isinstance(msg, dict) and msg.get("type") == "ping":
                ws.send(json.dumps({"type": "pong"}))
        ws.on_message(on_msg)
        ws.send("\x1b[?2004h".encode())


def new_page(browser, base, *, touch=False, term=None, mock_terminal=True, api=None):
    ctx = browser.new_context(viewport={"width": 420, "height": 800} if touch else {"width": 1000, "height": 700},
                              has_touch=touch, service_workers="block")
    ctx.add_init_script(INIT_JS)
    pg = ctx.new_page()
    if mock_terminal:
        pg.route_web_socket("**/api/ws/terminal**", term.handler)
    pg.route_web_socket("**/api/ws/events**", lambda ws: None)
    pg.route("**/api/**", api or api_route)
    pg.goto(base, wait_until="domcontentloaded")
    pg.wait_for_selector(".xterm-helper-textarea", state="attached", timeout=20000)
    pg.wait_for_timeout(2500)
    return ctx, pg


def bracketed(text: str) -> bytes:
    return BP0 + text.encode() + BP1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dist")
    ap.add_argument("--items", required=True, help="comma-separated ledger ids: " + ",".join(ITEMS))
    ap.add_argument("--out")
    ap.add_argument("--only", default="", help="comma-separated scenario names (debugging; the ledger never uses it)")
    a = ap.parse_args()
    wanted = [s for i in a.items.split(",") if i for s in ITEMS[i.strip()]]
    if a.only:
        wanted = [s for s in wanted if s in a.only.split(",")]
    dist = Path(a.dist).resolve()
    assert (dist / "index.html").exists(), f"no index.html in {dist}"
    srv = serve(dist)
    base = f"http://127.0.0.1:{srv.server_address[1]}/"
    results: dict = {}

    def record(name, ok, detail):
        results[name] = {"ok": bool(ok), "detail": detail}
        print(f"  .. {name} {'ok' if ok else 'failed'}", file=sys.stderr, flush=True)

    with sync_playwright() as p:
        b = p.chromium.launch()
        desk_term = Term()
        dctx, dpg = new_page(b, base, term=desk_term)

        def bytes_after(term, action, wait=1200):
            term.sent.clear()
            action()
            dpg.wait_for_timeout(wait)
            return bytes(term.sent)

        # ---- C-15: typing paths --------------------------------------------------------
        for mode in ("insertText", "composition"):
            if f"dictation-{mode}" in wanted:
                raw = bytes_after(desk_term, lambda: dpg.evaluate(DICT_JS, [PHRASES, mode]), 300)
                got = apply_bs(raw)
                record(f"dictation-{mode}", got == "take a look at it.", {"text": got, "raw": raw.hex()})
        for mode in ("insertText", "composition"):
            if f"dictation-rewrite-{mode}" in wanted:
                raw = bytes_after(desk_term, lambda: dpg.evaluate(DICT_JS, [REWRITE, mode]), 300)
                got = apply_bs(raw)
                record(f"dictation-rewrite-{mode}", got == "take a look at it.", {"text": got, "raw": raw.hex()})
        if "predictive" in wanted:
            raw = bytes_after(desk_term, lambda: dpg.evaluate(PREDICTIVE_JS), 300)
            record("predictive", raw == b"hi there ", {"raw": repr(raw)})
        if "hardware" in wanted:
            def hw():
                dpg.locator(".xterm-helper-textarea").focus()
                dpg.keyboard.type("hello world", delay=30)
            raw = bytes_after(desk_term, hw, 800)
            record("hardware", raw == b"hello world", {"raw": repr(raw)})
        if "paste-breakout" in wanted:
            raw = bytes_after(desk_term, lambda: dpg.evaluate(PASTE_EVENT_JS, [BREAKOUT, False]))
            record("paste-breakout", raw == bracketed(BREAKOUT_CLEAN), {"raw": repr(raw)})

        # ---- C-2: every paste path -----------------------------------------------------
        if "paste-native" in wanted:
            raw = bytes_after(desk_term, lambda: dpg.evaluate(PASTE_EVENT_JS, [PASTE, False]))
            record("paste-native", raw == bracketed(PASTE_CLEAN), {"raw": repr(raw)})
        if "paste-ctrlv" in wanted:
            def ctrlv():
                dpg.evaluate(CTRL_V_JS)
                dpg.evaluate(PASTE_EVENT_JS, [PASTE, False])
            raw = bytes_after(desk_term, ctrlv)
            record("paste-ctrlv", raw == bracketed(PASTE_CLEAN), {"raw": repr(raw)})
        if "paste-keyboard" in wanted:
            def kbd():
                dpg.evaluate("t => { window.__clipText = t; }", PASTE)
                dpg.evaluate(PASTE_EVENT_JS, [None, False])          # Ctrl+Shift+V with an empty text/plain
            raw = bytes_after(desk_term, kbd)
            record("paste-keyboard", raw == bracketed(PASTE_CLEAN), {"raw": repr(raw)})
        if "paste-controls" in wanted:
            def ctl():
                dpg.evaluate("t => { window.__clipText = t; }", PASTE)
                dpg.locator("button.trig-ctl").click()
                dpg.locator("button.kb", has_text=re.compile(r"^Paste$")).click()
            raw = bytes_after(desk_term, ctl)
            if dpg.locator(".pop").count():
                dpg.locator("button.trig-ctl").click()
            record("paste-controls", raw == bracketed(PASTE_CLEAN), {"raw": repr(raw)})
        if "paste-image" in wanted:
            raw = bytes_after(desk_term, lambda: dpg.evaluate(PASTE_EVENT_JS, [None, True]), 2000)
            record("paste-image", raw == bracketed(IMG_PATH_CLEAN), {"raw": repr(raw)})
        if "osc52-cap" in wanted:
            cap = osc52_max_bytes()
            small = "e2e-osc52-small"
            desk_term.ws.send(b"\x1b]52;c;" + base64.b64encode(small.encode()) + b"\x07")
            dpg.wait_for_timeout(800)
            clip = dpg.evaluate("() => window.__clip.slice()")
            small_ok = small in clip
            dpg.evaluate("() => { window.__clip.length = 0; window.__toasts.length = 0; }")
            big = b"A" * (cap + 1)
            desk_term.ws.send(b"\x1b]52;c;" + base64.b64encode(big) + b"\x07")
            dpg.wait_for_timeout(2000)
            clip = dpg.evaluate("() => window.__clip.map(s => s.length)")
            toasts = dpg.evaluate("() => window.__toasts.slice()")
            ignored = not any(n >= cap for n in clip)
            warned = any(t.split("|", 1)[0].split().count("bad") for t in toasts)
            record("osc52-cap", small_ok and ignored and warned,
                   {"small_copied": small_ok, "big_ignored": ignored, "bad_toast": warned, "toasts": toasts[-3:]})
        dctx.close()

        # ---- C-15: touch keyboards (Controls shows the key rows) ------------------------
        if "ctrl-composition" in wanted or "ctrl-key" in wanted or "tray-clear" in wanted:
            t_term = Term()
            tctx, tpg = new_page(b, base, touch=True, term=t_term)

            def key(label):
                return tpg.locator("button.kb", has=tpg.locator("span.kl-k", has_text=re.compile(f"^{re.escape(label)}$")))

            def ensure_controls():
                if tpg.locator(".pop").count() == 0:
                    tpg.locator("button.trig-ctl").click()
                    tpg.wait_for_timeout(200)

            def ta_value():
                return tpg.evaluate("() => document.querySelector('.xterm-helper-textarea').value")
            tpg.locator(".xterm-helper-textarea").focus()
            tpg.locator("button.trig-ctl").click()
            if "ctrl-composition" in wanted:
                key("Ctrl").first.click()
                tpg.wait_for_timeout(200)
                armed = tpg.locator(".mod-ind").first.is_visible()
                t_term.sent.clear()
                tpg.evaluate(COMPOSE_JS, "c")
                tpg.wait_for_timeout(600)
                raw = bytes(t_term.sent)
                cleared = not tpg.locator(".mod-ind").first.is_visible()
                record("ctrl-composition", armed and raw == b"\x03" and cleared,
                       {"indicator_shown": armed, "raw": repr(raw), "indicator_cleared": cleared})
            if "ctrl-key" in wanted:
                # soft-key Ctrl once, then a hardware-keyboard "c" (onData from xterm's keydown)
                tpg.wait_for_timeout(500)
                if tpg.locator(".mod-ind").first.is_visible():      # start from "off" whatever ran before
                    tpg.locator(".mod-ind").first.click()
                    tpg.wait_for_timeout(200)
                ensure_controls()
                key("Ctrl").first.click()
                tpg.wait_for_timeout(200)
                armed = tpg.locator(".mod-ind").first.is_visible()
                t_term.sent.clear()
                tpg.locator(".xterm-helper-textarea").focus()
                tpg.keyboard.type("c")
                tpg.wait_for_timeout(500)
                raw = bytes(t_term.sent)
                cleared = not tpg.locator(".mod-ind").first.is_visible()
                record("ctrl-key", armed and raw == b"\x03" and cleared,
                       {"indicator_shown": armed, "raw": repr(raw), "indicator_cleared": cleared})
            if "tray-clear" in wanted:
                out = {}
                ensure_controls()
                for label, want in (("Enter", b"\r"), ("Ctrl+C", b"\x03")):
                    tpg.evaluate(COMPOSE_JS, "abc")
                    before = ta_value()
                    t_term.sent.clear()
                    key(label).first.click()
                    tpg.wait_for_timeout(500)
                    out[label] = {"before": before, "after": ta_value(), "raw": repr(bytes(t_term.sent)),
                                  "ok": bool(before) and ta_value() == "" and bytes(t_term.sent) == want}
                record("tray-clear", all(v["ok"] for v in out.values()), out)
            tctx.close()

        # ---- D-6: connection feedback ----------------------------------------------------
        if "close-message" in wanted:
            c_term = Term(close_seq=[(1013, "e2e-marker-1013 terminal limit"),
                                     (4503, "e2e-marker-4503 herdr server not running")])
            cctx, cpg = new_page(b, base, term=c_term)
            end = time.time() + 20
            seen = []
            while time.time() < end:
                c_term.flush_closes()
                seen = cpg.evaluate("() => window.__seen.slice()")
                if {"e2e-marker-1013", "e2e-marker-4503"} <= set(seen):
                    break
                cpg.wait_for_timeout(100)
            record("close-message", {"e2e-marker-1013", "e2e-marker-4503"} <= set(seen),
                   {"seen": seen, "connections": c_term.connections})
            cctx.close()
        if "never-open" in wanted:
            Holder.ws_hits.clear()
            nctx, npg = new_page(b, base, mock_terminal=False)
            end = time.time() + 30
            while time.time() < end and Holder.ws_hits.get("/api/ws/terminal", 0) < 2:
                npg.wait_for_timeout(250)
            hits = Holder.ws_hits.get("/api/ws/terminal", 0)
            record("never-open", hits >= 2, {"terminal_handshakes": hits})
            nctx.close()
        if "reset-mid-composition" in wanted:
            # the socket drops mid-composition; an update while it is down cannot be sent (send() false);
            # after the reopen the next correction must not backspace text the new client never got
            r_term = Term()
            rctx, rpg = new_page(b, base, term=r_term)
            rpg.evaluate(COMP_STEP_JS, ["start", ""])
            rpg.evaluate(COMP_STEP_JS, ["update", "hel"])
            rpg.wait_for_timeout(300)
            first = r_term.connections
            r_term.ws.close(code=1011, reason="e2e drop")
            rpg.wait_for_timeout(30)
            rpg.evaluate(COMP_STEP_JS, ["update", "hell"])          # socket down: send() is false
            end = time.time() + 15
            while time.time() < end and r_term.connections == first:
                rpg.wait_for_timeout(100)
            rpg.wait_for_timeout(500)
            r_term.sent.clear()
            rpg.evaluate(COMP_STEP_JS, ["update", "hex"])
            rpg.evaluate(COMP_STEP_JS, ["end", "hex"])
            rpg.wait_for_timeout(800)
            raw = bytes(r_term.sent)
            reopened = r_term.connections > first
            record("reset-mid-composition", reopened and b"\x7f" not in raw and b"x" in raw,
                   {"reopened": reopened, "raw_after_reopen": repr(raw)})
            rctx.close()
        for name, status in (("mic-blocked", "blocked"), ("mic-enter", "idle")):
            if name not in wanted:
                continue
            log: list = []
            m_term = Term()
            mctx, mpg = new_page(b, base, term=m_term, api=make_api_route(status, log))
            mpg.locator("button.mic-btn").click()
            mpg.wait_for_timeout(1800)
            mpg.locator("button.mic-btn").click()                    # stop: the fake heard words
            posts = lambda: [pth for m, pth in log if m == "POST"]
            end = time.time() + (6 if status == "blocked" else 20)
            while time.time() < end and not any("send-keys" in p_ for p_ in posts()):
                mpg.wait_for_timeout(250)
            sent_text = any("send-text" in p_ for p_ in posts())
            entered = any("send-keys" in p_ for p_ in posts())
            if status == "blocked":
                record(name, not sent_text and not entered, {"posts": posts()})
            else:
                order = [p_ for p_ in posts() if "send-" in p_]
                record(name, sent_text and entered and "send-text" in order[0], {"posts": posts()})
            mctx.close()
        b.close()
    srv.shutdown()

    missing = [s for s in wanted if s not in results]
    for s in missing:
        record(s, False, "scenario did not run")
    ok = all(results[s]["ok"] for s in wanted)
    for s in wanted:
        print(f"{'PASS' if results[s]['ok'] else 'FAIL'} {s}: {json.dumps(results[s]['detail'])[:300]}")
    if a.out:
        Path(a.out).write_text(json.dumps({"items": a.items, "ok": ok, "scenarios": results}, indent=1) + "\n")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
