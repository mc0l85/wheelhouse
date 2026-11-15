#!/usr/bin/env python3
"""Phone acceptance (ledger items V-1, V-2, V-3) in Playwright WebKit with an iPhone profile.

  python3 frontend/e2e/acceptance_v.py <dist-dir> --items V-1,V-2,V-3 [--shots DIR] [--out result.json]

These replace the owner's real-phone sign-off (PRD 6.1). They run ON THE BUILD VM (check_vm.sh), never
on the dev host. Same harness as acceptance_e2e.py: <dist-dir> is served on 127.0.0.1, every /api route
is mocked, the terminal WebSocket is acceptance_e2e.Term (ping -> pong, bracketed paste on) and every
byte the page sends is asserted exactly. The page runs in WebKit (Safari's engine) with the iPhone
device profile (touch, mobile viewport, iOS user agent); the soft-keyboard tray (Controls) is driven
with real taps (`locator.click()`).

  V-1  dictation (insertText and composition, extending and rewriting) and iOS predictive text give
       exact bytes; the tray Enter after a dictation sends exactly "\\r" and clears the textarea.
  V-2  tray Ctrl / Alt: one tap = one-shot (the next letter only, indicator clears), a double tap locks
       (every letter until a further tap releases it).
  V-3  Mic auto-Enter: a page frozen through the countdown (a locked phone) fires the timer late ->
       no Enter, the text stays, a warning toast says so; an unfrozen countdown still sends Enter.

--shots DIR saves one PNG per scenario (gitignored results directory) for a later model review.
Exit 1 if any selected scenario fails.
"""
import argparse, json, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from acceptance_e2e import (COMP_STEP_JS, COMPOSE_JS, INIT_JS, Term, api_route,  # noqa: E402
                            make_api_route, serve)
from run_e2e import DICT_JS, PHRASES, PREDICTIVE_JS, apply_bs  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

DEVICE = "iPhone 15"
REWRITE = ["take", "take a", "take a lock", "take a look at it."]

ITEMS = {
    "V-1": ["v-dictation-insertText", "v-dictation-composition", "v-dictation-rewrite-insertText",
            "v-dictation-rewrite-composition", "v-predictive", "v-tray-enter-after-dictation"],
    "V-2": ["v-ctrl-once", "v-ctrl-lock", "v-alt-once", "v-alt-lock"],
    "V-3": ["v-mic-late-enter-skipped", "v-mic-on-time-enter"],
}


def new_phone_page(p, browser, base, *, term=None, api=None):
    dev = dict(p.devices[DEVICE])
    assert dev.get("has_touch") and dev.get("is_mobile") is not False, f"{DEVICE} profile lost touch"
    ctx = browser.new_context(**dev, service_workers="block")
    ctx.add_init_script(INIT_JS)
    pg = ctx.new_page()
    if term is not None:
        pg.route_web_socket("**/api/ws/terminal**", term.handler)
    pg.route_web_socket("**/api/ws/events**", lambda ws: None)
    pg.route("**/api/**", api or api_route)
    pg.goto(base, wait_until="domcontentloaded")
    pg.wait_for_selector(".xterm-helper-textarea", state="attached", timeout=20000)
    pg.wait_for_timeout(2500)
    return ctx, pg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dist")
    ap.add_argument("--items", required=True, help="comma-separated ledger ids: " + ",".join(ITEMS))
    ap.add_argument("--shots", help="directory for one screenshot per scenario")
    ap.add_argument("--out")
    a = ap.parse_args()
    wanted = [s for i in a.items.split(",") if i for s in ITEMS[i.strip()]]
    dist = Path(a.dist).resolve()
    assert (dist / "index.html").exists(), f"no index.html in {dist}"
    shots = Path(a.shots) if a.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    srv = serve(dist)
    base = f"http://127.0.0.1:{srv.server_address[1]}/"
    results: dict = {}

    def record(name, ok, detail, pg=None):
        if shots and pg is not None:
            try:
                pg.screenshot(path=str(shots / f"{name}.png"))
                detail = {**detail, "screenshot": f"{name}.png"} if isinstance(detail, dict) else detail
            except Exception as e:                       # a screenshot failure never hides the result
                detail = {**detail, "screenshot_error": str(e)} if isinstance(detail, dict) else detail
        results[name] = {"ok": bool(ok), "detail": detail}
        print(f"  .. {name} {'ok' if ok else 'failed'}", file=sys.stderr, flush=True)

    with sync_playwright() as p:
        b = p.webkit.launch()
        assert b.browser_type.name == "webkit"

        def tray(pg):
            def key(label):
                return pg.locator("button.kb", has=pg.locator("span.kl-k", has_text=re.compile(f"^{re.escape(label)}$")))

            def ensure_controls():
                if pg.locator(".pop").count() == 0:
                    pg.locator("button.trig-ctl").click()
                    pg.wait_for_timeout(250)
            return key, ensure_controls

        def ind_visible(pg):
            return pg.locator(".mod-ind").count() > 0 and pg.locator(".mod-ind").first.is_visible()

        def release_mods(pg):
            for _ in range(3):
                if not ind_visible(pg):
                    return
                pg.locator(".mod-ind").first.click()
                pg.wait_for_timeout(300)

        # ---- V-1: dictation / predictive / tray Enter -----------------------------------
        try:
            if any(s in wanted for s in ITEMS["V-1"]):
                term = Term()
                ctx, pg = new_phone_page(p, b, base, term=term)
                pg.locator(".xterm-helper-textarea").focus()

                def bytes_after(action, wait=400):
                    term.sent.clear()
                    action()
                    pg.wait_for_timeout(wait)
                    return bytes(term.sent)
                for mode in ("insertText", "composition"):
                    for kind, phrases in (("", PHRASES), ("rewrite-", REWRITE)):
                        name = f"v-dictation-{kind}{mode}"
                        if name in wanted:
                            pg.wait_for_timeout(1700)            # outside any earlier commit's duplicate window
                            pg.evaluate("() => { const t = document.querySelector('.xterm-helper-textarea'); t.value = ''; t.focus(); }")
                            raw = bytes_after(lambda: pg.evaluate(DICT_JS, [phrases, mode]))
                            got = apply_bs(raw)
                            record(name, got == "take a look at it.", {"text": got, "raw": raw.hex()}, pg)
                if "v-predictive" in wanted:
                    pg.wait_for_timeout(1700)
                    pg.evaluate("() => { const t = document.querySelector('.xterm-helper-textarea'); t.value = ''; t.focus(); }")
                    raw = bytes_after(lambda: pg.evaluate(PREDICTIVE_JS))
                    record("v-predictive", raw == b"hi there ", {"raw": repr(raw)}, pg)
                if "v-tray-enter-after-dictation" in wanted:
                    key, ensure_controls = tray(pg)
                    pg.wait_for_timeout(1700)
                    pg.evaluate("() => { const t = document.querySelector('.xterm-helper-textarea'); t.value = ''; t.focus(); }")
                    term.sent.clear()
                    pg.evaluate(DICT_JS, [["ok", "ok go"], "insertText"])
                    pg.wait_for_timeout(400)
                    typed = bytes(term.sent)
                    ensure_controls()
                    term.sent.clear()
                    key("Enter").first.click()
                    pg.wait_for_timeout(600)
                    after = bytes(term.sent)
                    ta = pg.evaluate("() => document.querySelector('.xterm-helper-textarea').value")
                    record("v-tray-enter-after-dictation", apply_bs(typed) == "ok go" and after == b"\r" and ta == "",
                           {"typed": repr(typed), "enter": repr(after), "textarea_after": ta}, pg)
                ctx.close()

        except Exception as e:                  # one broken scenario must not hide the others
            for s in ITEMS["V-1"]:
                if s in wanted and s not in results:
                    results[s] = {"ok": False, "detail": {"error": f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"}}

        # ---- V-2: tray Ctrl / Alt one-shot and lock -------------------------------------
        try:
            if any(s in wanted for s in ITEMS["V-2"]):
                term = Term()
                ctx, pg = new_phone_page(p, b, base, term=term)
                key, ensure_controls = tray(pg)
                pg.locator(".xterm-helper-textarea").focus()

                def compose(ch):
                    pg.evaluate(COMPOSE_JS, ch)
                    pg.wait_for_timeout(150)

                for mod, pre in (("ctrl", None), ("alt", b"\x1b")):
                    label = "Ctrl" if mod == "ctrl" else "Alt"

                    def enc(ch: str) -> bytes:
                        return bytes([ord(ch) & 0x1f]) if mod == "ctrl" else pre + ch.encode()
                    if f"v-{mod}-once" in wanted:
                        release_mods(pg)
                        ensure_controls()
                        key(label).first.click()
                        pg.wait_for_timeout(700)                 # past the double-tap window: once, not locked
                        armed = ind_visible(pg)
                        term.sent.clear()
                        compose("c")
                        first = bytes(term.sent)
                        cleared = not ind_visible(pg)
                        term.sent.clear()
                        compose("c")
                        second = bytes(term.sent)
                        record(f"v-{mod}-once", armed and first == enc("c") and cleared and second == b"c",
                               {"armed": armed, "first": repr(first), "indicator_cleared": cleared,
                                "second": repr(second)}, pg)
                    if f"v-{mod}-lock" in wanted:
                        release_mods(pg)
                        ensure_controls()
                        key(label).first.click()
                        pg.wait_for_timeout(80)                  # inside the double-tap window
                        key(label).first.click()
                        pg.wait_for_timeout(500)
                        term.sent.clear()
                        compose("a")
                        compose("b")
                        locked = bytes(term.sent)
                        still = ind_visible(pg)
                        pg.screenshot(path=str(shots / f"v-{mod}-lock-armed.png")) if shots else None
                        ensure_controls()
                        key(label).first.click()                   # a further tap releases the lock
                        pg.wait_for_timeout(700)
                        released = not ind_visible(pg)
                        term.sent.clear()
                        compose("d")
                        after = bytes(term.sent)
                        record(f"v-{mod}-lock", locked == enc("a") + enc("b") and still and released and after == b"d",
                               {"locked": repr(locked), "indicator_while_locked": still, "released": released,
                                "after_release": repr(after)}, pg)
                ctx.close()

        except Exception as e:                  # one broken scenario must not hide the others
            for s in ITEMS["V-2"]:
                if s in wanted and s not in results:
                    results[s] = {"ok": False, "detail": {"error": f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"}}

        # ---- V-3: Mic auto-Enter guard ----------------------------------------------------
        try:
            for name, freeze in (("v-mic-late-enter-skipped", True), ("v-mic-on-time-enter", False)):
                if name not in wanted:
                    continue
                log: list = []
                term = Term()
                ctx, pg = new_phone_page(p, b, base, term=term, api=make_api_route("idle", log))
                posts = lambda: [pth for m, pth in log if m == "POST"]   # noqa: E731
                pg.locator("button.mic-btn").click()
                pg.wait_for_timeout(1800)
                pg.locator("button.mic-btn").click()                        # stop: the fake recognizer heard words
                end = time.time() + 10
                while time.time() < end and not any("send-text" in x for x in posts()):
                    pg.wait_for_timeout(100)
                delivered = any("send-text" in x for x in posts())
                if freeze:
                    pg.wait_for_timeout(7000)                             # countdown is SUBMIT_MS = 10 s
                    # a locked phone: the page's JS is frozen past the due time, then resumes ~3 s late
                    pg.evaluate("() => { const t = Date.now(); while (Date.now() - t < 6000) {} }")
                    pg.wait_for_timeout(4000)
                    toasts = pg.evaluate("() => window.__toasts.slice()")
                    entered = any("send-keys" in x for x in posts())
                    late_toast = any(t.split("|", 1)[0].split().count("bad") and "late" in t for t in toasts)
                    record(name, delivered and not entered and late_toast,
                           {"posts": posts(), "toasts": toasts[-3:]}, pg)
                else:
                    end = time.time() + 20
                    while time.time() < end and not any("send-keys" in x for x in posts()):
                        pg.wait_for_timeout(250)
                    order = [x for x in posts() if "send-" in x]
                    record(name, delivered and len(order) >= 2 and "send-text" in order[0] and "send-keys" in order[1],
                           {"posts": posts()}, pg)
                ctx.close()
        except Exception as e:                  # one broken scenario must not hide the others
            for s in ITEMS["V-3"]:
                if s in wanted and s not in results:
                    results[s] = {"ok": False, "detail": {"error": f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"}}

        b.close()
    srv.shutdown()

    for s in wanted:
        if s not in results:
            record(s, False, "scenario did not run")
    ok = all(results[s]["ok"] for s in wanted)
    for s in wanted:
        print(f"{'PASS' if results[s]['ok'] else 'FAIL'} {s}: {json.dumps(results[s]['detail'])[:300]}")
    if a.out:
        Path(a.out).write_text(json.dumps({"items": a.items, "engine": "webkit", "device": DEVICE,
                                           "ok": ok, "scenarios": results}, indent=1) + "\n")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
