import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api/client";
import type { Health, Pane, PaneScroll, Tree } from "./api/types";
import { Controls } from "./components/Controls";
import { Inbox } from "./components/Inbox";
import { Mic } from "./components/Mic";
import { ArrowRow, ComboRow, KeyRow, type Mods, noBlur, ShortcutRow } from "./components/Keys";
import { PaneSelector, paneName } from "./components/PaneSelector";
import { type HerdrEvent, useHerdrEvents } from "./hooks/useHerdrEvents";
import { type ConnState, copyText, useHerdrTerminal } from "./hooks/useHerdrTerminal";
import { pasteImage, readClipboard } from "./paste";

// herdr renders its own workspace/tab/pane chrome inside the TUI, so the page is
// one slim bar (pane selector · Controls) over a full-bleed live herdr terminal.
// Popovers OVERLAY the terminal so opening them never resizes the PTY.

// Touch device → keys live in Controls (and the optional pinned tray).
const IS_TOUCH = typeof window !== "undefined" &&
  (window.matchMedia?.("(pointer: coarse)").matches || navigator.maxTouchPoints > 0);

const PIN_KEY = "wheelhouse.pinKeys";
function loadPin(): boolean {
  try { return localStorage.getItem(PIN_KEY) === "1"; } catch { return false; }
}

type Pop = null | "panes" | "controls" | "inbox";
interface Toast { msg: string; bad: boolean; }

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [tree, setTree] = useState<Tree | null>(null);
  const [open, setOpen] = useState<Pop>(null);
  const [mods, setMods] = useState<Mods>({ ctrl: false, alt: false });
  const [pinned, setPinnedState] = useState(loadPin);
  const [toast, setToast] = useState<Toast | null>(null);
  const [conn, setConn] = useState<ConnState>("connecting");
  const [scroll, setScroll] = useState<Record<string, PaneScroll>>({});
  const [badgePulse, setBadgePulse] = useState(false);
  const toastTimer = useRef(0);

  const flash = useCallback((msg: string, bad = false) => {
    setToast({ msg, bad });
    clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), bad ? 3000 : 1500);
  }, []);

  const session = health?.session ?? "default";
  const interactive = !!health?.writes_enabled;
  const onConn = useCallback((s: ConnState) => {
    if (s === "restored") { flash("reconnected ✓"); setConn("open"); } else setConn(s);
  }, [flash]);
  const onPasteImage = useCallback((img: Blob) => {
    void pasteImage(img, (t) => apiRef.current?.paste(t), flash);
  }, [flash]);   // apiRef is read at call time (declared just below)
  const { hostRef, apiRef } = useHerdrTerminal(session, interactive, true,
    (n) => flash(`copied ${n} chars ✓`), onConn, onPasteImage);

  // A sticky modifier lives in the terminal effect; a new session resets it.
  useEffect(() => { setMods({ ctrl: false, alt: false }); }, [session]);

  // Size the app from the VISUAL viewport: in an iOS standalone PWA 100vh does
  // not shrink when the soft keyboard shows.
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    // offsetTop: iOS pans the visual viewport to keep xterm's caret textarea in
    // view; pin the app to it so the bar never scrolls off the top.
    const set = () => {
      const r = document.documentElement.style;
      r.setProperty("--vvh", `${vv.height}px`);
      r.setProperty("--vvo", `${vv.offsetTop}px`);
    };
    set();
    vv.addEventListener("resize", set);
    vv.addEventListener("scroll", set);
    return () => { vv.removeEventListener("resize", set); vv.removeEventListener("scroll", set); };
  }, []);

  // Track the page's visibility so polls stop while the PWA is backgrounded.
  const [hidden, setHidden] = useState(document.hidden);
  useEffect(() => {
    const f = () => setHidden(document.hidden);
    document.addEventListener("visibilitychange", f);
    return () => document.removeEventListener("visibilitychange", f);
  }, []);

  useEffect(() => {
    if (hidden) return;
    // Keep the last good health on a failed poll: a network blip must not
    // flip the UI to read-only or hide the keys.
    const tick = () => api.health().then(setHealth).catch(() => {});
    tick();
    const t = setInterval(tick, 10000);
    return () => clearInterval(t);
  }, [hidden]);

  // Pane tree: pushed by herdr events (debounced refetch); polling is the
  // fallback — 4 s while the event stream is down, 15 s while it is live.
  const refetch = useRef(0);
  const loadTree = useCallback(() => api.tree().then(setTree).catch(() => {}), []);
  const live = useHerdrEvents(useCallback((e: HerdrEvent) => {
    if (e.type === "event" && e.event === "pane.scroll_changed" && e.pane_id && e.scroll) {
      const id = e.pane_id, sc = e.scroll;
      setScroll((m) => ({ ...m, [id]: sc }));
      return;
    }
    clearTimeout(refetch.current);
    refetch.current = window.setTimeout(loadTree, 150);
  }, [loadTree]));
  useEffect(() => {
    if (hidden) return;
    loadTree();
    const t = setInterval(loadTree, live ? 15000 : open === "panes" ? 2000 : 4000);
    return () => clearInterval(t);
  }, [open, live, hidden, loadTree]);

  // Close on a pointerdown outside any popover/trigger. A dismiss tap on the
  // terminal is consumed (no compat mouse events) so it never clicks a herdr
  // tab or pane hidden under the popover; xterm keeps focus either way.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const el = e.target as Element | null;
      if (el?.closest("[data-pop],[data-trigger]")) return;
      if (el?.closest(".stage")) { e.preventDefault(); e.stopPropagation(); }
      setOpen(null);
    };
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, [open]);

  // A tapped push notification lands here: ?inbox=<pane> on a cold start, or a
  // message from the service worker when the app was already open.
  useEffect(() => {
    const u = new URL(location.href);
    if (u.searchParams.has("inbox")) {
      setOpen("inbox");
      u.searchParams.delete("inbox");
      history.replaceState(null, "", u.pathname + u.search);
    }
    const onMsg = (e: MessageEvent) => { if (e.data?.type === "open-inbox") setOpen("inbox"); };
    navigator.serviceWorker?.addEventListener("message", onMsg);
    return () => navigator.serviceWorker?.removeEventListener("message", onMsg);
  }, []);

  const setPinned = (v: boolean) => {
    setPinnedState(v);
    try { localStorage.setItem(PIN_KEY, v ? "1" : "0"); } catch { /* private mode */ }
  };

  const panes: Pane[] = tree ? tree.workspaces.flatMap((w) => w.tabs.flatMap((t) => t.panes)) : [];
  const focused = panes.find((p) => p.focused);
  const focusedTab = tree?.workspaces.flatMap((w) => w.tabs).find((t) => t.tab_id === focused?.tab_id);
  const blocked = panes.filter((p) => p.agent_status === "blocked").length;
  // Event offsets are fresher than the tree's; fall back to the tree's.
  const focusedScroll = focused ? (scroll[focused.pane_id] ?? focused.scroll) : undefined;
  const scrolledUp = focusedScroll?.offset_from_bottom ?? 0;

  // A rising blocked count pulses the badge; the Home Screen icon shows it too.
  const prevBlocked = useRef(0);
  useEffect(() => {
    if (blocked > prevBlocked.current) {
      setBadgePulse(true);
      window.setTimeout(() => setBadgePulse(false), 1600);
    }
    prevBlocked.current = blocked;
    const nav = navigator as Navigator & { setAppBadge?: (n: number) => Promise<void>; clearAppBadge?: () => Promise<void> };
    if (blocked > 0) nav.setAppBadge?.(blocked).catch(() => {});
    else nav.clearAppBadge?.().catch(() => {});
  }, [blocked]);

  async function copyPane(source: "visible" | "recent") {
    setOpen(null);
    try {
      const pane = focused ?? (await api.panes()).panes.find((p) => p.focused);
      if (!pane) { flash("no pane", true); return; }
      const { content } = await api.paneText(pane.pane_id, source, source === "recent" ? 2000 : 400);
      const ok = await copyText(content.replace(/\s+$/g, "") + "\n");
      if (!ok) throw new Error("clipboard write refused");
      flash(`copied ${paneName(pane)} ✓`);
    } catch (e) {
      flash("copy failed (clipboard blocked)", true);
      console.error(e);
    }
  }

  async function paste() {
    try {
      const { image, text } = await readClipboard();
      if (image) onPasteImage(image);
      else if (text) apiRef.current?.paste(text);
      else flash("clipboard is empty", true);
    } catch {
      flash("paste blocked — use Ctrl+Shift+V", true);
    }
  }

  async function pick(p: Pane) {
    setOpen(null);
    if (p.focused) { flash(`already on ${paneName(p)}`); return; }
    // Optimistic: move the focus mark now; the next poll reconciles.
    setTree((t) => t && {
      workspaces: t.workspaces.map((w) => ({ ...w, tabs: w.tabs.map((tb) => ({
        ...tb, panes: tb.panes.map((q) => ({ ...q, focused: q.pane_id === p.pane_id })),
      })) })),
    });
    try { await api.focusPane(p.pane_id); flash(`→ ${paneName(p)}`); }
    catch (e) { flash("focus failed", true); console.error(e); }
    loadTree();
  }

  async function zoom() {
    if (!focused) return;
    try {
      await api.zoomPane(focused.pane_id);
      const l = await api.layout(focused.pane_id);
      flash(l.zoomed ? `zoomed ${paneName(focused)} ⤢` : "unzoomed");
    } catch (e) { flash(`zoom failed: ${(e as Error).message}`, true); }
  }

  async function toBottom() {
    if (!focused) return;
    try {
      const l = await api.layout(focused.pane_id);
      const r = l.panes.find((q) => q.pane_id === focused.pane_id)?.rect;
      if (!r) throw new Error("pane not on screen");
      apiRef.current?.scrollToBottom(r, scrolledUp);
    } catch (e) { flash(`scroll failed: ${(e as Error).message}`, true); }
  }

  const toggle = (which: Exclude<Pop, null>) => setOpen((o) => (o === which ? null : which));

  return (
    <div className={`app ${pinned && IS_TOUCH && interactive ? "pinned" : ""}`}>
      <header className="bar">
        <button {...noBlur} data-trigger className={`trig ${open === "panes" ? "on" : ""}`}
          onClick={() => toggle("panes")}>
          <span className={`sdot s-${focused?.agent_status ?? "unknown"}`} />
          <span className="trig-label">
            {focused ? `${paneName(focused)} · ${focusedTab?.label ?? ""}` : "panes"}
          </span>
          <span className="caret">▾</span>
        </button>
        {blocked > 0 && (
          <button {...noBlur} data-trigger className={`badge-btn ${badgePulse ? "pulse" : ""} ${open === "inbox" ? "on" : ""}`}
            title="blocked agents — tap to answer" onClick={() => toggle("inbox")}>⚠ {blocked}</button>
        )}
        <span className="bar-grow" />
        {scrolledUp > 0 && (
          <button {...noBlur} className="bar-chip scroll-chip" title="scrolled back — tap to return to the bottom"
            onClick={toBottom}>↓ {scrolledUp} up</button>
        )}
        {conn !== "open" && <span className="bar-ind conn" title="reconnecting to herdr">⟳ {conn}</span>}
        {health && !interactive && <span className="bar-ind" title="read-only">🔒</span>}
        {(mods.ctrl || mods.alt) && (
          <button {...noBlur} className="kb on mod-ind" title="modifier armed — tap to release"
            onClick={() => {
              if (mods.ctrl) apiRef.current?.toggleCtrl();
              if (mods.alt) apiRef.current?.toggleAlt();
              setMods({ ctrl: false, alt: false });
            }}>{mods.ctrl ? "Ctrl" : "Alt"} armed</button>
        )}
        {interactive && <Mic flash={flash} />}
        {interactive && focused && (
          <button {...noBlur} className="trig trig-zoom" title="zoom / unzoom the focused pane" onClick={zoom}>⤢</button>
        )}
        <button {...noBlur} data-trigger className={`trig trig-ctl ${open === "controls" ? "on" : ""}`}
          onClick={() => toggle("controls")}>
          {(!health || !health.server_running) && <span className="sdot s-blocked" />}
          Controls <span className="caret">▾</span>
        </button>
      </header>

      <main className="stage">
        <div ref={hostRef} className="terminal-host" />
      </main>

      {pinned && IS_TOUCH && interactive && (
        <footer className="tray" {...noBlur}>
          <ShortcutRow api={apiRef} />
          <KeyRow api={apiRef} mods={mods} setMods={setMods} />
          <ArrowRow api={apiRef} />
          <ComboRow api={apiRef} />
        </footer>
      )}

      {open === "panes" && <PaneSelector tree={tree} onPick={pick} />}
      {open === "inbox" && <Inbox panes={panes} writes={interactive} onOpen={pick} flash={flash} />}
      {open === "controls" && (
        <Controls api={apiRef} health={health} paneCount={panes.length} touch={IS_TOUCH}
          mods={mods} setMods={setMods} pinned={pinned} setPinned={setPinned}
          onCopy={copyPane} onPaste={paste} flash={flash} />
      )}
      {toast && <div className={`toast ${toast.bad ? "bad" : ""}`}>{toast.msg}</div>}
    </div>
  );
}
