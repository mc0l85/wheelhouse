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
//  • Ctrl/Alt are STICKY modifiers: arm, then the next soft-keyboard char is
//    transformed (Ctrl+c → 0x03, Alt+x → ESC x).
import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { CanvasAddon } from "@xterm/addon-canvas";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { clipboardImage, readClipboard } from "../paste";

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

export interface TermApi {
  type: (s: string) => void;          // raw text/sequence
  ctrlCombo: (letter: string) => void; // e.g. "c" → ^C
  toggleCtrl: () => boolean;          // sticky; returns new armed state
  toggleAlt: () => boolean;
  focus: () => void;                  // pop the soft keyboard
  paste: (text: string) => void;      // bracketed paste into the PTY
  armed: () => { ctrl: boolean; alt: boolean };
  scrollToBottom: (rect: { x: number; y: number; width: number; height: number }, lines: number) => void;
  fontStep: (delta: number) => number;  // ±px, clamped + remembered; returns new size
  fontSize: () => number;
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

export type ConnState = "connecting" | "open" | "reconnecting" | "restored";

export function useHerdrTerminal(
  session: string, interactive: boolean, mouseToHerdr: boolean,
  onCopied?: (chars: number) => void,
  onConn?: (s: ConnState) => void,
  onPasteImage?: (img: Blob) => void,
) {
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
    term.loadAddon(new WebLinksAddon((_e, uri) => openLink(uri)));
    term.open(host);
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
    let ws: WebSocket | null = null;
    let disposed = false;
    let lastDims = "";
    const armed = { ctrl: false, alt: false };

    const raw = (s: string) => {
      if (interactiveRef.current && ws && ws.readyState === WebSocket.OPEN) ws.send(enc.encode(s));
    };
    const ctrlOf = (ch: string) => {
      const c = ch.toLowerCase().charCodeAt(0);
      if (c >= 97 && c <= 122) return String.fromCharCode(c - 96); // a→^A … z→^Z
      if (ch === " ") return "\x00";
      return ch;
    };
    // Apply sticky modifiers (lock semantics — cleared by tapping the key again)
    // to soft/physical typed chars, then send.
    const onKeystroke = (d: string) => {
      let out = d;
      if (armed.ctrl && d.length === 1) out = ctrlOf(d);
      if (armed.alt) out = "\x1b" + out;
      raw(out);
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
    const oscSub = term.parser.registerOscHandler(52, (data) => {
      const i = data.indexOf(";");
      const payload = i >= 0 ? data.slice(i + 1) : data;
      if (!payload || payload === "?") return true;
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
    //    is uploaded, anything else goes to the PTY as ^V like before, so Claude
    //    Code's own Ctrl+V and a shell's quoted-insert keep working.
    //  • Chromium's Ctrl+Shift+V is "paste as plain text": an image-only
    //    clipboard arrives EMPTY, and a screenshot tool's (KDE Spectacle) arrives
    //    as just its file URL. Either way, ask the async Clipboard API for the
    //    image; the text is pasted if there is none.
    let ctrlV = 0;                     // timer: a Ctrl+V paste is expected
    const ctrlVDone = (sendCtrlV: boolean) => {
      if (!ctrlV) return;
      clearTimeout(ctrlV); ctrlV = 0;
      if (sendCtrlV) raw("\x16");
    };
    const onPaste = (e: ClipboardEvent) => {
      const up = interactiveRef.current ? pasteImgRef.current : undefined;
      const img = up ? clipboardImage(e.clipboardData) : null;
      if (img && up) {
        ctrlVDone(false);
        e.preventDefault(); e.stopPropagation();
        up(img);
        return;
      }
      if (ctrlV) {
        ctrlVDone(true);
        e.preventDefault(); e.stopPropagation();
        return;
      }
      const dt = e.clipboardData;
      const plain = dt?.getData("text/plain") ?? "";
      if (up && dt && navigator.clipboard?.read && (!dt.types.length || IMAGE_FILE_RE.test(plain.trim()))) {
        e.preventDefault(); e.stopPropagation();
        void readClipboard().then(({ image, text }) => {
          if (image) up(image); else if (text || plain) term.paste(text || plain);
        }).catch(() => { if (plain) term.paste(plain); });   // permission refused
      }
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
      if (ws && ws.readyState === WebSocket.OPEN) {
        termStats.ptyResizes++;
        ws.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
      }
    };
    // The soft keyboard animation fires ~10 viewport resizes; collapse them.
    let resizeTimer = 0;
    const debouncedResize = () => {
      clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(sendResize, 100);
    };
    // --- connection lifecycle: auto-reconnect + heartbeat -------------------
    // herdr is a persistent server, so a fresh client re-attaches and redraws the
    // whole screen; nothing is lost by reconnecting. Sockets die silently when
    // iOS suspends the PWA or a VPN re-keys, so a heartbeat detects zombies.
    let retry = 0;
    let retryTimer = 0;
    let pingTimer = 0;
    let pongDeadline = 0;
    let everOpened = false;
    const PING_MS = 15000, PONG_WAIT_MS = 5000;

    const sendPing = (waitMs = PONG_WAIT_MS) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      try { ws.send(JSON.stringify({ type: "ping" })); } catch { /* close handler retries */ }
      clearTimeout(pongDeadline);
      pongDeadline = window.setTimeout(abandon, waitMs);   // no pong → zombie
    };
    // A zombie never completes the close handshake, so onclose may not fire for
    // minutes: detach it, drop it, and reconnect now.
    function abandon() {
      const dead = ws;
      if (!dead) return;
      ws = null;
      dead.onclose = null; dead.onmessage = null; dead.onopen = null;
      try { dead.close(); } catch { /* already gone */ }
      clearInterval(pingTimer); clearTimeout(pongDeadline);
      if (!disposed) { term.reset(); scheduleReconnect(); }
    }
    const scheduleReconnect = (delay?: number) => {
      if (disposed) return;
      clearTimeout(retryTimer);
      const d = delay ?? Math.min(250 * 2 ** retry, 5000);
      retry++;
      connRef.current?.("reconnecting");
      retryTimer = window.setTimeout(connect, d);
    };
    const reconnectNow = () => {
      if (disposed) return;
      if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
        retry = 0; scheduleReconnect(0);
      } else if (ws.readyState === WebSocket.OPEN) {
        sendPing(3000);   // back from background: prove the socket is alive, fast
      }
    };

    const onVisible = () => {
      if (document.hidden) return;
      reconnectNow();
      sendResize();      // diff-guarded: a wake must not resize everyone's herdr
      term.focus();
    };

    function connect() {
      if (disposed) return;
      try { fit.fit(); } catch { /* ignore */ }
      lastDims = `${term.rows}x${term.cols}`;
      connRef.current?.(everOpened ? "reconnecting" : "connecting");
      const sock = new WebSocket(
        `${proto}://${location.host}/api/ws/terminal` +
        `?rows=${term.rows}&cols=${term.cols}`,
      );
      ws = sock;
      sock.binaryType = "arraybuffer";
      sock.onmessage = (ev) => {
        if (typeof ev.data === "string") {          // server text frames are control only
          if (ev.data.includes('"pong"')) clearTimeout(pongDeadline);
          return;
        }
        clearTimeout(pongDeadline);                  // any output proves liveness
        const s = dec.decode(new Uint8Array(ev.data), { stream: true });
        term.write(mouseToHerdr ? s : s.replace(MOUSE_RE, ""));
      };
      sock.onopen = () => {
        if (everOpened) { dec = new TextDecoder(); connRef.current?.("restored"); }
        everOpened = true;
        retry = 0;
        connRef.current?.("open");
        lastDims = "";
        sendResize();
        clearInterval(pingTimer);
        pingTimer = window.setInterval(() => sendPing(), PING_MS);
      };
      sock.onclose = () => {
        if (ws !== sock) return;                     // superseded socket
        clearInterval(pingTimer); clearTimeout(pongDeadline);
        if (!disposed) {
          // herdr's re-attach repaints everything; clear stale modes first.
          term.reset();
          scheduleReconnect();
        }
      };
    }

    const dataSub = term.onData(onKeystroke);
    term.attachCustomKeyEventHandler((e) => {
      if (e.type !== "keydown") return true;
      const cmd = e.metaKey, ctrl = e.ctrlKey, shift = e.shiftKey;
      // Ctrl+Shift+Enter → insert a newline (ESC+CR = meta/Option-Enter, which
      // Claude Code and most TUIs treat as newline rather than submit).
      if (e.code === "Enter" && ctrl && shift) { raw("\x1b\r"); return false; }
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
      type: (s) => raw(s),
      ctrlCombo: (letter) => raw(ctrlOf(letter)),
      toggleCtrl: () => { armed.ctrl = !armed.ctrl; if (armed.ctrl) armed.alt = false; term.focus(); return armed.ctrl; },
      toggleAlt: () => { armed.alt = !armed.alt; if (armed.alt) armed.ctrl = false; term.focus(); return armed.alt; },
      focus: () => term.focus(),
      paste: (text) => { if (interactiveRef.current) term.paste(text); },
      armed: () => ({ ...armed }),
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
    const rafId = requestAnimationFrame(() => connect());
    if (document.fonts?.ready) document.fonts.ready.then(() => sendResize());
    const t1 = window.setTimeout(sendResize, 150);
    const t2 = window.setTimeout(sendResize, 600);

    return () => {
      disposed = true;
      clearTimeout(t1); clearTimeout(t2); clearTimeout(resizeTimer);
      clearTimeout(retryTimer); clearInterval(pingTimer); clearTimeout(pongDeadline);
      window.removeEventListener("online", reconnectNow);
      window.removeEventListener("pageshow", reconnectNow);
      cancelAnimationFrame(rafId);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
      ro.disconnect();
      host.removeEventListener("touchstart", onTouchStart);
      host.removeEventListener("touchmove", onTouchMove);
      dataSub.dispose();
      oscSub.dispose();
      host.removeEventListener("mouseup", onMouseUp);
      host.removeEventListener("paste", onPaste, true);
      ws?.close();
      term.dispose();
      apiRef.current = null;
    };
  }, [session, mouseToHerdr]);

  return { hostRef, apiRef };
}
