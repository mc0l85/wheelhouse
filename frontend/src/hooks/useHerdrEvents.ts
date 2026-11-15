// herdr's pushed events (backend /api/ws/events). Each frame just says
// "something changed" (plus scroll offsets); the caller refetches /api/tree.
// `live` is false while the stream is down, so the caller keeps polling.
import { useEffect, useRef, useState } from "react";
import type { PaneScroll } from "../api/types";

export interface HerdrEvent {
  type: "event" | "stream";
  event?: string;
  pane_id?: string | null;
  scroll?: PaneScroll;
  agent_status?: string;
  up?: boolean;
}

export function useHerdrEvents(onEvent: (e: HerdrEvent) => void) {
  const [live, setLive] = useState(false);
  const cb = useRef(onEvent);
  cb.current = onEvent;

  useEffect(() => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    let ws: WebSocket | null = null;
    let retry = 0, timer = 0, disposed = false;

    const connect = () => {
      if (disposed) return;
      const sock = new WebSocket(`${proto}://${location.host}/api/ws/events`);
      ws = sock;
      sock.onmessage = (m) => {
        let e: HerdrEvent;
        try { e = JSON.parse(m.data); } catch { return; }
        if (e.type === "stream") setLive(!!e.up);
        retry = 0;
        cb.current(e);
      };
      sock.onclose = () => {
        if (ws !== sock || disposed) return;
        setLive(false);
        timer = window.setTimeout(connect, Math.min(500 * 2 ** retry++, 10000));
      };
    };
    // iOS kills sockets while the PWA is backgrounded: reconnect on return.
    const onVisible = () => {
      if (document.hidden || disposed) return;
      if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
        clearTimeout(timer); retry = 0; connect();
      }
    };
    connect();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      disposed = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      ws?.close();
    };
  }, []);

  return live;
}
