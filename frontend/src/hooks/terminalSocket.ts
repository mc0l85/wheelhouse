// Socket lifecycle for the herdr terminal: connect, onopen/onclose, ping/pong
// heartbeat, reconnect with backoff. Split out of useHerdrTerminal (H0) with no
// behaviour change.
//
// herdr is a persistent server, so a fresh client re-attaches and redraws the
// whole screen; nothing is lost by reconnecting. Sockets die silently when
// iOS suspends the PWA or a VPN re-keys, so a heartbeat detects zombies.
import type { ConnInfo } from "../types/term";
import { CONNECT_TIMEOUT_MS, onClose } from "./socketPolicy";

export interface TerminalSocketOptions {
  /** Runs at the top of every connect (fit the terminal, remember dims). */
  beforeConnect: () => void;
  /** WebSocket URL for the next connect (read after beforeConnect). */
  url: () => string;
  onConn: (info: ConnInfo) => void;
  /** A binary frame arrived (liveness already recorded). */
  onData: (bytes: Uint8Array) => void;
  /** Fired on every (re)open before the connection state is announced. */
  onReset: (reopened: boolean) => void;
  /** Fired on every (re)open after the state is announced (send the size). */
  onOpen: () => void;
  /** The socket was lost and a reconnect is scheduled (clear stale modes). */
  onLost: () => void;
}

export interface TerminalSocket {
  connect: () => void;
  /** Send bytes; false when the socket is not open. */
  send: (bytes: Uint8Array) => boolean;
  /** Send a JSON control frame; false when the socket is not open. */
  sendJson: (obj: unknown) => boolean;
  isOpen: () => boolean;
  /** The socket object currently in use (identity checks only). */
  current: () => WebSocket | null;
  reconnectNow: () => void;
  /** Drop the current socket and reconnect; optionally restart the backoff. */
  abandon: (resetBackoff?: boolean) => void;
  dispose: () => void;
}

const PING_MS = 15000, PONG_WAIT_MS = 5000;
/** An OPEN socket counts as established after its first binary frame or this long up. */
const ESTABLISHED_MS = 5000;

export function createTerminalSocket(opts: TerminalSocketOptions): TerminalSocket {
  let ws: WebSocket | null = null;
  let disposed = false;
  let retry = 0;
  let retryTimer = 0;
  let pingTimer = 0;
  let pongDeadline = 0;
  let everOpened = false;   // some socket was established (not merely OPEN)
  let failStreak = 0;       // consecutive sockets that closed before being established
  let connectTimer = 0;

  const isOpen = () => !!ws && ws.readyState === WebSocket.OPEN;

  const sendPing = (waitMs = PONG_WAIT_MS) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try { ws.send(JSON.stringify({ type: "ping" })); } catch { /* close handler retries */ }
    clearTimeout(pongDeadline);
    pongDeadline = window.setTimeout(() => abandon(), waitMs);   // no pong → zombie
  };
  // A zombie never completes the close handshake, so onclose may not fire for
  // minutes: detach it, drop it, and reconnect now.
  function abandon(resetBackoff = false) {
    if (resetBackoff) retry = 0;
    const dead = ws;
    if (!dead) return;
    ws = null;
    dead.onclose = null; dead.onmessage = null; dead.onopen = null;
    try { dead.close(); } catch { /* already gone */ }
    clearInterval(pingTimer); clearTimeout(pongDeadline); clearTimeout(connectTimer);
    if (!disposed) { opts.onLost(); scheduleReconnect(); }
  }
  const scheduleReconnect = (delay?: number, message?: string) => {
    if (disposed) return;
    clearTimeout(retryTimer);
    const d = delay ?? Math.min(250 * 2 ** retry, 5000);
    retry++;
    opts.onConn(message ? { state: "reconnecting", message } : { state: "reconnecting" });
    retryTimer = window.setTimeout(connect, d);
  };
  const reconnectNow = () => {
    if (disposed) return;
    if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
      retry = 0; scheduleReconnect(0);
    } else if (ws.readyState === WebSocket.CONNECTING) {
      // A handshake that may be stuck: replace it now rather than wait for it.
      const pending = ws;
      ws = null;
      clearTimeout(connectTimer);
      pending.onclose = null; pending.onmessage = null; pending.onopen = null;
      try { pending.close(); } catch { /* already gone */ }
      retry = 0; scheduleReconnect(0);
    } else if (ws.readyState === WebSocket.OPEN) {
      sendPing(3000);   // back from background: prove the socket is alive, fast
    }
  };

  function connect() {
    if (disposed) return;
    opts.beforeConnect();
    opts.onConn({ state: everOpened ? "reconnecting" : "connecting" });
    // Exactly one socket per terminal. The first connect runs from
    // requestAnimationFrame, which a background tab holds back, while
    // pageshow/online can schedule another; any older socket left open is a
    // second herdr client whose diffs interleave with this one's in the same
    // xterm (ghost text that never clears, SGR tails like "5;49m" as text).
    clearTimeout(retryTimer); clearTimeout(connectTimer);
    if (ws) {
      const old = ws;
      old.onmessage = null; old.onopen = null; old.onclose = null;
      try { old.close(); } catch { /* already gone */ }
    }
    const sock = new WebSocket(opts.url());
    ws = sock;
    sock.binaryType = "arraybuffer";
    let opened = false;          // reached OPEN
    let established = false;     // first binary frame, or ESTABLISHED_MS up
    let establishTimer = 0;
    // A refused socket is accepted first and then closed with a code, so OPEN
    // alone proves nothing: only now reset the backoff and the terminal.
    const establish = () => {
      if (established || ws !== sock) return;
      established = true;
      clearTimeout(establishTimer);
      opts.onReset(everOpened);
      if (everOpened) opts.onConn({ state: "restored" });
      everOpened = true;
      failStreak = 0;
      retry = 0;
      opts.onConn({ state: "open" });
    };
    // A handshake that never completes (dead route, stalled proxy) is abandoned
    // and counted like a refused one.
    connectTimer = window.setTimeout(() => {
      if (ws !== sock || disposed) return;
      ws = null;
      sock.onclose = null; sock.onmessage = null; sock.onopen = null;
      try { sock.close(); } catch { /* already gone */ }
      failStreak++;
      const dec = onClose(1006, "connect timeout", false, failStreak);
      opts.onLost();
      scheduleReconnect(dec.delayMs, dec.message);
    }, CONNECT_TIMEOUT_MS);
    sock.onmessage = (ev) => {
      if (ws !== sock) return;                     // superseded socket
      if (typeof ev.data === "string") {          // server text frames are control only
        if (ev.data.includes('"pong"')) clearTimeout(pongDeadline);
        return;
      }
      clearTimeout(pongDeadline);                  // any output proves liveness
      establish();
      opts.onData(new Uint8Array(ev.data));
    };
    sock.onopen = () => {
      if (ws !== sock) return;
      opened = true;
      clearTimeout(connectTimer);
      establishTimer = window.setTimeout(establish, ESTABLISHED_MS);
      opts.onOpen();
      clearInterval(pingTimer);
      pingTimer = window.setInterval(() => sendPing(), PING_MS);
    };
    sock.onclose = (ev) => {
      if (ws !== sock) return;                     // superseded socket
      clearInterval(pingTimer); clearTimeout(pongDeadline); clearTimeout(connectTimer); clearTimeout(establishTimer);
      if (!disposed) {
        if (!established) failStreak++;
        const dec = onClose(ev?.code ?? 1006, ev?.reason ?? "", established, established ? 0 : failStreak);
        // herdr's re-attach repaints everything; clear stale modes first.
        opts.onLost();
        // 1013 on an established socket is the server shedding a slow reader
        // (output backlog), not a refusal: reconnect quickly, no error banner.
        if (established && (ev?.code ?? 0) === 1013) { scheduleReconnect(250); return; }
        scheduleReconnect(dec.delayMs, dec.message);
      }
    };
  }

  return {
    connect,
    send: (bytes) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return false;
      ws.send(bytes);
      return true;
    },
    sendJson: (obj) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return false;
      ws.send(JSON.stringify(obj));
      return true;
    },
    isOpen,
    current: () => ws,
    reconnectNow,
    abandon,
    dispose: () => {
      disposed = true;
      clearTimeout(retryTimer); clearInterval(pingTimer); clearTimeout(pongDeadline); clearTimeout(connectTimer);
      ws?.close();
    },
  };
}
