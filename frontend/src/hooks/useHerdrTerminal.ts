// Live, interactive herdr terminal over a WebSocket↔PTY bridge.
//
//  • Mouse goes to herdr (tap/click panes). herdr's own drag-select (pane-bounded,
//    scrollback-aware) comes back as OSC 52, which we turn into a clipboard write;
//    Shift-drag still makes a native xterm selection and is copied on mouse-up.
//  • Paste (Ctrl+Shift+V / Shift+Insert / Cmd+V) rides the browser's native paste
//    event → term.paste() (bracketed paste, no clipboard-read permission prompt).
//    An image on the clipboard goes to onPasteImage instead (see paste.ts).
//  • Keystrokes go out as BINARY (so "1" isn't mistaken for a JSON control frame).
//  • Exposes an imperative API (apiRef) so an on-screen key bar can send Esc/Tab/
//    arrows/Ctrl-combos — essential on iOS/Android, which have no Ctrl/Esc keys.
//  • Ctrl/Alt are STICKY modifiers: tap arms one key (Ctrl+c → 0x03, Alt+x → ESC x),
//    a double-tap locks until tapped again.
import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { CanvasAddon } from "@xterm/addon-canvas";
import { WrappedLinkProvider } from "../wrapLinks";
import { clipboardImage, readClipboard } from "../paste";
import { restyleGhost, resetGhost } from "../dim";
import type { ConnInfo, ModState, TermApi } from "../types/term";
import { createTrace, TRACE } from "../input/trace";
import { OSC52_MAX_BYTES, sanitizePaste } from "../input/sanitize";
import { createModifiers, ctrlChar, type ModSource } from "../input/modifiers";
import {
  DUP_WINDOW_MS, initialComposerState, reduce,
  type ComposerEvent, type DataOrigin, type ModSnapshot, type TextareaSnapshot,
} from "../input/composer";
import { createTerminalSocket } from "./terminalSocket";

// Clipboard write that survives non-gesture / insecure-context callers: async
// Clipboard API first, then a synchronous hidden-textarea execCommand fallback.
function legacyCopy(text: string): boolean {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  ta.remove();
  return ok;
}
export async function copyText(text: string): Promise<boolean> {
  if (!text) return false;
  try {
    if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return true; }
  } catch { /* fall through to the legacy path */ }
  return legacyCopy(text);
}

function openLink(uri: string) {
  if (/^https?:\/\//i.test(uri)) window.open(uri, "_blank", "noopener,noreferrer");
}

// A lone image file path or file:// URL: what a screenshot tool leaves as text.
const IMAGE_FILE_RE = /^(file:\/\/)?\S+\.(png|jpe?g|gif|webp)$/i;

const MOUSE_RE =/\x1b\[\?(1000|1001|1002|1003|1005|1006|1015|1016)[hl]/g;

export type { TermApi, ModState, ConnInfo, ConnState } from "../types/term";

const RING = 256 * 1024;   // bytes of output kept for a glitch report (matches the backend)
function b64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

const FONT_KEY = "wheelhouse.fontSize";
const FONT_DEFAULT = 13, FONT_MIN = 9, FONT_MAX = 24;
function loadFontSize(): number {
  try {
    const n = Number(localStorage.getItem(FONT_KEY));
    if (n >= FONT_MIN && n <= FONT_MAX) return n;
  } catch { /* private mode */ }
  return FONT_DEFAULT;
}

const RENDERER_KEY = "wheelhouse.renderer";
export type Renderer = "canvas" | "dom";
export function rendererPref(): Renderer {
  if (new URLSearchParams(location.search).has("dom")) return "dom";
  try { return localStorage.getItem(RENDERER_KEY) === "dom" ? "dom" : "canvas"; } catch { return "canvas"; }
}
export function setRendererPref(r: Renderer) {
  try { localStorage.setItem(RENDERER_KEY, r); } catch { /* private mode */ }
  location.reload();
}

// PTY resizes sent this page load. Shown in Controls › Session: it must stay flat
// while popovers open/close or the soft keyboard is used (each one is a herdr
// full redraw), so it is the failable check for layout/focus regressions.
export const termStats = { ptyResizes: 0 };

export function useHerdrTerminal(
  session: string, interactive: boolean, mouseToHerdr: boolean,
  onCopied?: (chars: number) => void,
  onConn?: (info: ConnInfo) => void,
  onPasteImage?: (img: Blob) => void,
  onNote?: (msg: string, bad?: boolean) => void,
  onMods?: (m: { ctrl: ModState; alt: ModState }) => void,
) {
  const modsRef = useRef(onMods);
  modsRef.current = onMods;
  const noteRef = useRef(onNote);
  noteRef.current = onNote;
  const hostRef = useRef<HTMLDivElement | null>(null);
  const apiRef = useRef<TermApi | null>(null);
  const copiedRef = useRef(onCopied);
  copiedRef.current = onCopied;
  const connRef = useRef(onConn);
  connRef.current = onConn;
  const pasteImgRef = useRef(onPasteImage);
  pasteImgRef.current = onPasteImage;
  // Read through a ref so a writes-flag change never tears down the terminal
  // and its WebSocket (that used to happen on every failed health poll).
  const interactiveRef = useRef(interactive);
  interactiveRef.current = interactive;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const term = new Terminal({
      fontFamily: '"JetBrains Mono", monospace',
      fontSize: loadFontSize(),
      cursorBlink: true,
      allowProposedApi: true,
      scrollback: 0,
      macOptionClickForcesSelection: true,   // ⌥-drag selects even when mouse is on
      rightClickSelectsWord: true,
      theme: { background: "#04070e", foreground: "#e6edff", cyan: "#38e1ff",
               magenta: "#a78bfa", green: "#6ee7a8", yellow: "#fcd34d" },
      // OSC 8 hyperlinks (herdr passes them through from panes) open in a new tab.
      linkHandler: { activate: (_e, uri) => openLink(uri), allowNonHttpProtocols: false },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    // Bare http(s) URLs in pane text: underline on hover, click opens a new tab.
    // Joins URLs herdr wrapped across rows (WebLinksAddon saw only the first row).
    const linkProvider = term.registerLinkProvider(new WrappedLinkProvider(term, openLink));
    term.open(host);
    // Chrome's own inline writing suggestions / autofill would land in xterm's
    // hidden textarea and show through its composition view as solid text over
    // the pane. The terminal app (Claude Code) draws its own suggestions.
    const ta = term.textarea;
    if (ta) {
      ta.setAttribute("writingsuggestions", "false");
      ta.setAttribute("autocomplete", "off");
      ta.setAttribute("data-gramm", "false");   // Grammarly-style extensions
    }
    // The canvas renderer draws box-drawing/block glyphs (herdr's borders and
    // pane scrollbars) as seamless custom glyphs, like a native terminal; the
    // DOM renderer takes them from the font and leaves gaps between rows.
    // Controls › Renderer (or ?dom) falls back to the DOM renderer.
    if (rendererPref() === "canvas") {
      try { term.loadAddon(new CanvasAddon()); } catch { /* DOM renderer */ }
    }

    const proto = location.protocol === "https:" ? "wss" : "ws";
    const enc = new TextEncoder();
    let dec = new TextDecoder();
    let disposed = false;
    let lastDims = "";
    // Sticky Ctrl/Alt (tap = once, double-tap = locked) live in input/modifiers.ts; the
    // composer (input/composer.ts) turns IME / dictation / autocorrect events into pane bytes.
    const mods = createModifiers({
      now: () => performance.now(),
      onChange: () => modsRef.current?.({ ctrl: mods.state("ctrl"), alt: mods.state("alt") }),
    });
    const raw = (s: string): boolean => (interactiveRef.current ? socket.send(enc.encode(s)) : true);

    // Soft keyboards (iOS predictive text, Android Gboard) type a word as an IME
    // composition, and xterm sends nothing until the word commits. Until then
    // the pane never sees the keys: a Claude Code suggestion stays on screen and
    // xterm's composition overlay is drawn on top of it ("h" over "run it…" =
    // "hun it…"). So the composition is sent live, as backspaces plus the new
    // tail of the word, and xterm's own send at commit is replaced by the last
    // correction. The overlay is hidden in wheel.css: the pane echoes for real.
    // iOS dictation REPLACES its previous text in xterm's textarea (every
    // hypothesis selects the old one and inserts the whole phrase again); the
    // composer erases only what differs, per grapheme.
    const trace = createTrace(ta);   // no-op unless built with VITE_INPUT_TRACE=1
    let cs = initialComposerState();
    const timers = new Map<string, number>();
    let composing = false;           // between compositionstart and compositionend
    let keydownNow = false, keydownDataSeen = false;   // inside a textarea keydown dispatch
    let commitUntil = 0;             // xterm's commit-time onData may arrive until then
    let pasting = false;             // inside term.paste()
    const taSnap = (): TextareaSnapshot => {
      const v = ta?.value ?? "";
      return { value: v, selStart: ta?.selectionStart ?? v.length, selEnd: ta?.selectionEnd ?? v.length };
    };
    const snap = (): ModSnapshot => ({ ctrl: mods.state("ctrl"), alt: mods.state("alt") });
    const dispatch = (event: ComposerEvent, source?: ModSource) => {
      const r = reduce(cs, event);
      cs = r.state;
      for (const e of r.effects) {
        if (e.type === "setTimer") {
          clearTimeout(timers.get(e.id));
          timers.set(e.id, window.setTimeout(() => {
            timers.delete(e.id);
            dispatch({ type: "timeout", now: performance.now(), id: e.id });
          }, e.ms));
        } else if (e.type === "cancelTimer") {
          clearTimeout(timers.get(e.id)); timers.delete(e.id);
        } else if (e.type === "clearTextarea") {
          clearTa();
        } else if (e.type === "toast") {
          noteRef.current?.(e.msg, true);
        } else mods.consume(e.m);
      }
      if (!r.send) return;
      if (TRACE) trace("sent", { text: r.send });
      if (!raw(source ? mods.apply(r.send, source) : r.send)) dispatch({ type: "sendFailed", now: performance.now() });
    };
    const clearTa = () => {
      if (!ta) return;
      ta.value = "";
      try { ta.setSelectionRange(0, 0); } catch { /* detached */ }
    };
    const onBeforeInput = (e: InputEvent) => {
      if (TRACE) trace("beforeinput", { inputType: e.inputType, data: e.data, composing: e.isComposing });
      if (!ta || e.isComposing) return;
      const t = taSnap();
      dispatch({ type: "beforeinput", now: performance.now(), inputType: e.inputType, data: e.data, ta: t, mods: snap() });
    };
    const onInput = (e: Event) => {
      const ie = e as InputEvent;
      if (TRACE) trace("input", { inputType: ie.inputType, data: ie.data, composing: ie.isComposing });
      if (!ta) return;
      dispatch({ type: "input", now: performance.now(), inputType: ie.inputType, ta: taSnap() });
    };
    const onCompStart = () => {
      composing = true; commitUntil = 0;
      if (TRACE) trace("compositionstart");
      dispatch({ type: "compositionstart", now: performance.now(), ta: taSnap(), mods: snap() });
    };
    const onCompUpdate = (e: CompositionEvent) => {
      if (TRACE) trace("compositionupdate", { data: e.data });
      dispatch({ type: "compositionupdate", now: performance.now(), data: e.data ?? "", ta: taSnap(), mods: snap() });
    };
    const onCompEnd = (e: CompositionEvent) => {
      composing = false;
      const now = performance.now();
      commitUntil = now + DUP_WINDOW_MS;
      if (TRACE) trace("compositionend", { data: e.data });
      dispatch({ type: "compositionend", now, data: e.data ?? "", ta: taSnap() });
    };
    // Called from attachCustomKeyEventHandler, which xterm runs FIRST in its keydown/keypress
    // handling: everything xterm emits for this key (a keydown composition flush, the key's own
    // data) is produced after this flag is set, and the flag drops once the event has finished.
    let keyClear = 0;
    const markKey = (e: KeyboardEvent) => {
      if (e.type === "keyup") return;   // a keyup must not re-arm the flag: xterm's commit timer may fire after it
      if (e.type === "keydown") { keydownNow = true; keydownDataSeen = false; } else keydownNow = true;
      if (!composing && e.keyCode !== 229) commitUntil = 0;   // a real key: no commit is pending any more
      clearTimeout(keyClear);
      keyClear = window.setTimeout(() => { keydownNow = false; }, 0);
    };
    // Focus / mouse reports xterm generates are never modified.
    const REPORT_RE = /^\x1b\[(?:[IO]|<\d+;\d+;\d+[Mm]|M[\s\S]{3})/;
    const onBlur = () => {
      composing = false;
      dispatch({ type: "blur", now: performance.now() });
    };
    const onKeystroke = (d: string) => {
      if (TRACE) trace("onData", { data: d.length > 80 ? d.slice(0, 80) + "…" : d });
      const now = performance.now();
      let origin: DataOrigin = "other";
      if (composing && (cs.comp !== null || cs.abandoned === "live") && keydownNow && !keydownDataSeen) origin = "keydownFinalize";
      else if (!keydownNow && !pasting && now < commitUntil) { origin = "commit"; commitUntil = 0; }
      if (keydownNow) keydownDataSeen = true;
      const source: ModSource = pasting ? "paste" : REPORT_RE.test(d) ? "report" : "key";
      // Composition flushes and commits are not typed keys: no Ctrl/Alt on them.
      dispatch({ type: "onData", now, data: d, origin }, origin === "other" ? source : undefined);
    };
    // Every paste goes through the sanitiser (ESC / C0 / C1 dropped: no bracketed-paste breakout).
    const pasteText = (text: string) => {
      const clean = sanitizePaste(text);
      if (!clean) return;
      pasting = true;
      try { term.paste(clean); } finally { pasting = false; }
    };
    const copied = (text: string) =>
      copyText(text).then((ok) => { if (ok) copiedRef.current?.(text.length); term.focus(); return ok; });
    const copySelection = () => {
      const sel = term.getSelection();
      if (sel) void copied(sel);
      return !!sel;
    };
    // herdr drag-select → OSC 52 (";c;<base64>") → system clipboard. Query form
    // ("?") is ignored: the page never reads the clipboard back to the TUI.
    let oscToastAt = 0;           // rate limit for the over-cap toast
    const oscSub = term.parser.registerOscHandler(52, (data) => {
      const i = data.indexOf(";");
      const payload = i >= 0 ? data.slice(i + 1) : data;
      if (!payload || payload === "?") return true;
      const bytes = Math.floor((payload.length * 3) / 4) - (payload.endsWith("==") ? 2 : payload.endsWith("=") ? 1 : 0);
      if (bytes > OSC52_MAX_BYTES) {
        const t = performance.now();
        if (!oscToastAt || t - oscToastAt > 5000) {
          oscToastAt = t;
          noteRef.current?.("clipboard copy ignored: too large", true);
        }
        return true;
      }
      try {
        const bin = atob(payload);
        const text = new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
        if (text) void copied(text);
      } catch { /* malformed base64 */ }
      return true;
    });
    // Shift-drag native selection: copy on release, then clear so a later click
    // can't re-copy a stale selection over herdr's.
    const onMouseUp = () => { if (term.hasSelection()) { copySelection(); term.clearSelection(); } };
    host.addEventListener("mouseup", onMouseUp);
    // A clipboard image never reaches xterm (it would paste nothing): capture
    // the paste before xterm's textarea sees it and hand the image up.
    //  • Ctrl+V is let through as a native paste (see the key handler): an image
    //    is uploaded, text is pasted, and an empty clipboard sends ^V (a shell's
    //    quoted-insert). Text used to send ^V too, so copied text never arrived:
    //    Claude Code's own Ctrl+V reads the SERVER's clipboard, which has none.
    //  • Chromium's Ctrl+Shift+V is "paste as plain text": an image-only
    //    clipboard arrives EMPTY, and a screenshot tool's (KDE Spectacle) arrives
    //    as just its file URL. Either way, ask the async Clipboard API for the
    //    image; the text is pasted if there is none.
    let ctrlV = 0;                     // timer: a Ctrl+V paste is expected
    const ctrlVDone = () => {
      const was = !!ctrlV;
      clearTimeout(ctrlV); ctrlV = 0;
      return was;
    };
    const empty = () => noteRef.current?.("clipboard is empty — nothing pasted", true);
    const onPaste = (e: ClipboardEvent) => {
      const up = interactiveRef.current ? pasteImgRef.current : undefined;
      const img = up ? clipboardImage(e.clipboardData) : null;
      const wasCtrlV = ctrlVDone();
      if (img && up) {
        e.preventDefault(); e.stopPropagation();
        up(img);
        return;
      }
      const dt = e.clipboardData;
      const plain = dt?.getData("text/plain") ?? "";
      if (wasCtrlV) {
        // Ctrl+V's native paste carries any image, so none here means none on
        // the clipboard: text is pasted like Ctrl+Shift+V; only an empty
        // clipboard still sends a raw ^V (shell quoted-insert).
        e.preventDefault(); e.stopPropagation();
        if (plain) pasteText(plain); else raw("\x16");
        return;
      }
      if (up && typeof navigator.clipboard?.read === "function" && (!plain.trim() || IMAGE_FILE_RE.test(plain.trim()))) {
        e.preventDefault(); e.stopPropagation();
        void readClipboard().then(({ image, text }) => {
          if (image) up(image); else if (text || plain) pasteText(text || plain); else empty();
        }).catch(() => { if (plain) pasteText(plain); else empty(); });   // permission refused
        return;
      }
      if (!plain) { empty(); return; }   // xterm pastes only text/plain: say so instead of nothing
      // Native text paste: take it over from xterm so it is sanitised too.
      e.preventDefault(); e.stopPropagation();
      pasteText(plain);
    };
    host.addEventListener("paste", onPaste, true);

    const sendResize = () => {
      // Skip while the container is mid-layout (proposed dims NaN/0).
      const p = fit.proposeDimensions();
      if (!p || !p.cols || !p.rows || Number.isNaN(p.cols) || Number.isNaN(p.rows)) return;
      try { fit.fit(); } catch { return; }
      const dims = `${term.rows}x${term.cols}`;
      if (dims === lastDims) return;
      lastDims = dims;
      if (socket.isOpen()) {
        termStats.ptyResizes++;
        socket.sendJson({ type: "resize", rows: term.rows, cols: term.cols });
      }
    };
    // The soft keyboard animation fires ~10 viewport resizes; collapse them.
    let resizeTimer = 0;
    const debouncedResize = () => {
      clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(sendResize, 100);
    };
    // --- connection lifecycle lives in terminalSocket.ts ----------------------
    let ring: Uint8Array[] = [], ringLen = 0;       // what this socket received
    const socket = createTerminalSocket({
      beforeConnect: () => {
        try { fit.fit(); } catch { /* ignore */ }
        lastDims = `${term.rows}x${term.cols}`;
      },
      url: () => `${proto}://${location.host}/api/ws/terminal?rows=${term.rows}&cols=${term.cols}`,
      onConn: (info) => connRef.current?.(info),
      onData: (bytes) => {
        ring.push(bytes); ringLen += bytes.length;
        while (ringLen - ring[0].length > RING) ringLen -= ring.shift()!.length;
        const s = restyleGhost(dec.decode(bytes, { stream: true }));
        term.write(mouseToHerdr ? s : s.replace(MOUSE_RE, ""));
      },
      onReset: (reopened) => {
        dispatch({ type: "reset", now: performance.now() });   // the new client never got the old composition
        ring = []; ringLen = 0;
        resetGhost();
        if (reopened) dec = new TextDecoder();
      },
      onOpen: () => {
        lastDims = "";
        sendResize();
      },
      onLost: () => term.reset(),
    });
    const reconnectNow = () => socket.reconnectNow();

    const onVisible = () => {
      if (document.hidden) return;
      reconnectNow();
      sendResize();      // diff-guarded: a wake must not resize everyone's herdr
      term.focus();
    };

    const dataSub = term.onData(onKeystroke);
    ta?.addEventListener("beforeinput", onBeforeInput as EventListener, true);
    ta?.addEventListener("input", onInput, true);
    ta?.addEventListener("compositionstart", onCompStart);
    ta?.addEventListener("compositionupdate", onCompUpdate);
    ta?.addEventListener("compositionend", onCompEnd);
    ta?.addEventListener("blur", onBlur);
    term.attachCustomKeyEventHandler((e) => {
      markKey(e);
      const shiftEnter = (e.code === "Enter" || e.code === "NumpadEnter") && e.shiftKey && !e.altKey && !e.metaKey;
      // xterm runs this for keypress too: Shift+Enter's keypress would add a bare CR.
      if (e.type !== "keydown") return !shiftEnter;
      const cmd = e.metaKey, ctrl = e.ctrlKey, shift = e.shiftKey;
      // Shift+Enter and Ctrl+Shift+Enter → insert a newline (ESC+CR = meta/
      // Option-Enter, which Claude Code, agy and most TUIs treat as newline
      // rather than submit). xterm.js sends a bare CR for Shift+Enter, so
      // without this agy submitted half-typed multi-line prompts.
      if (shiftEnter) { raw("\x1b\r"); return false; }
      if (e.code === "KeyC" && (cmd || (ctrl && shift) || ctrl)) {
        if (cmd || (ctrl && shift)) { copySelection(); return false; }
        if (ctrl && term.hasSelection()) { copySelection(); term.clearSelection(); return false; }
        return true;
      }
      if ((e.code === "KeyV" && ctrl && shift) || (e.code === "Insert" && shift)) {
        // Let the browser fire its native paste event; xterm turns it into
        // term.paste() (bracketed paste). No readText(), no double paste.
        return false;
      }
      if (e.code === "KeyV" && ctrl && !shift && !e.altKey && !cmd && interactiveRef.current) {
        // Native paste fires next; onPaste uploads an image or sends ^V. If
        // the browser fires no paste at all, ^V still goes out.
        clearTimeout(ctrlV);
        ctrlV = window.setTimeout(() => { ctrlV = 0; raw("\x16"); }, 300);
        return false;
      }
      return true;
    });

    apiRef.current = {
      type: (s) => {
        const sent = raw(mods.apply(s, "tray"));
        // On-screen Enter / ^C end the line: drop whatever xterm's textarea still holds.
        if (s === "\r" || s === "\x03") { clearTa(); dispatch({ type: "reset", now: performance.now() }); }
        else if (!sent) dispatch({ type: "sendFailed", now: performance.now() });
      },
      ctrlCombo: (letter) => raw(ctrlChar(letter) ?? letter),
      toggleCtrl: () => { const r = mods.tap("ctrl"); term.focus(); return r; },
      toggleAlt: () => { const r = mods.tap("alt"); term.focus(); return r; },
      focus: () => term.focus(),
      paste: (text) => { if (interactiveRef.current) pasteText(text); },
      armed: () => snap(),
      // Scroll the pane whose cell rect is given (herdr layout cells) to the
      // bottom of its scrollback: one wheel-down per 3 lines, aimed at its centre.
      scrollToBottom: (rect, lines) => {
        const c = cellPx();
        const x = c.left + (rect.x + rect.width / 2) * c.w;
        const y = c.top + (rect.y + rect.height / 2) * c.h;
        for (let i = 0; i < Math.ceil(lines / LINES_PER_NOTCH) + 1; i++) wheel(x, y, 1);
      },
      fontStep: (delta) => {
        const fs = Math.min(FONT_MAX, Math.max(FONT_MIN, (term.options.fontSize ?? FONT_DEFAULT) + delta));
        term.options.fontSize = fs;
        try { localStorage.setItem(FONT_KEY, String(fs)); } catch { /* private mode */ }
        sendResize();      // new cell size → new cols/rows → one PTY resize
        return fs;
      },
      fontSize: () => term.options.fontSize ?? FONT_DEFAULT,
      // A garbled screen: report what this socket received (the backend saves
      // it beside what herdr sent), then drop the socket. The reconnect is a
      // fresh herdr client, which repaints everything; other clients are untouched.
      redraw: () => {
        if (socket.isOpen()) {
          const all = new Uint8Array(ringLen);
          let o = 0;
          for (const c of ring) { all.set(c, o); o += c.length; }
          try { socket.sendJson({ type: "glitch", client: b64(all.subarray(Math.max(0, all.length - RING))) }); } catch { /* reconnect anyway */ }
        }
        const dead = socket.current();
        window.setTimeout(() => { if (socket.current() === dead) socket.abandon(true); }, 300);
      },
    };

    // --- touch scrolling (iPad) ------------------------------------------------
    // xterm ignores touchmove while the app has mouse tracking on (herdr always
    // does), but it encodes wheel events as mouse reports. So a finger drag
    // becomes synthetic wheel events at the finger's position: herdr scrolls the
    // pane under the finger through its own scrollback. A short tap stays a tap.
    const screenEl = host.querySelector(".xterm-screen") as HTMLElement | null;
    const cellPx = () => {
      const r = (screenEl ?? host).getBoundingClientRect();
      return { w: r.width / term.cols, h: r.height / term.rows, left: r.left, top: r.top };
    };
    const wheel = (x: number, y: number, dir: 1 | -1) => {
      (screenEl ?? host).dispatchEvent(new WheelEvent("wheel", {
        deltaY: dir, deltaMode: WheelEvent.DOM_DELTA_LINE,
        clientX: x, clientY: y, bubbles: true, cancelable: true,
      }));
    };
    const LINES_PER_NOTCH = 3;       // herdr mouse_scroll_lines default
    let touchY = 0, touchX = 0, acc = 0, scrolling = false;
    const onTouchStart = (e: TouchEvent) => {
      if (e.touches.length !== 1) return;
      touchY = e.touches[0].clientY; touchX = e.touches[0].clientX; acc = 0; scrolling = false;
    };
    const onTouchMove = (e: TouchEvent) => {
      if (e.touches.length !== 1) return;
      const t = e.touches[0];
      const dy = t.clientY - touchY;
      if (!scrolling && Math.abs(dy) < 8) return;
      scrolling = true;
      e.preventDefault();               // no page bounce, no synthesized click
      touchY = t.clientY;
      acc += dy;
      const step = cellPx().h * LINES_PER_NOTCH;
      while (Math.abs(acc) >= step) {
        // finger down = content down = back into history (wheel up)
        wheel(touchX, t.clientY, acc > 0 ? -1 : 1);
        acc -= Math.sign(acc) * step;
      }
    };
    host.addEventListener("touchstart", onTouchStart, { passive: true });
    host.addEventListener("touchmove", onTouchMove, { passive: false });

    const ro = new ResizeObserver(debouncedResize);
    ro.observe(host);
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    window.addEventListener("online", reconnectNow);
    window.addEventListener("pageshow", reconnectNow);
    const rafId = requestAnimationFrame(() => socket.connect());
    if (document.fonts?.ready) document.fonts.ready.then(() => sendResize());
    // A font file that lands after the terminal opened (the 3 s preload cap in
    // main.tsx, or a unicode-range subset like latin-ext fetched the first time
    // é appears) leaves fallback-font glyphs in the canvas renderer's cache:
    // mixed baselines, wavy text. Drop the cache and redraw with the real font.
    const onFontLoaded = () => { term.clearTextureAtlas(); term.refresh(0, term.rows - 1); sendResize(); };
    document.fonts?.addEventListener("loadingdone", onFontLoaded);
    const t1 = window.setTimeout(sendResize, 150);
    const t2 = window.setTimeout(sendResize, 600);

    return () => {
      disposed = true;
      clearTimeout(t1); clearTimeout(t2); clearTimeout(resizeTimer);
      window.removeEventListener("online", reconnectNow);
      window.removeEventListener("pageshow", reconnectNow);
      cancelAnimationFrame(rafId);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
      ro.disconnect();
      document.fonts?.removeEventListener("loadingdone", onFontLoaded);
      host.removeEventListener("touchstart", onTouchStart);
      host.removeEventListener("touchmove", onTouchMove);
      dataSub.dispose();
      for (const t of timers.values()) clearTimeout(t);
      trace.dispose();
      clearTimeout(keyClear);
      ta?.removeEventListener("beforeinput", onBeforeInput as EventListener, true);
      ta?.removeEventListener("input", onInput, true);
      ta?.removeEventListener("compositionstart", onCompStart);
      ta?.removeEventListener("compositionupdate", onCompUpdate);
      ta?.removeEventListener("compositionend", onCompEnd);
      ta?.removeEventListener("blur", onBlur);
      oscSub.dispose();
      host.removeEventListener("mouseup", onMouseUp);
      host.removeEventListener("paste", onPaste, true);
      socket.dispose();
      linkProvider.dispose();
      term.dispose();
      apiRef.current = null;
    };
  }, [session, mouseToHerdr]);

  return { hostRef, apiRef };
}
