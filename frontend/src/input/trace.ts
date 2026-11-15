// Dev-only input recorder (restored from 17d6d0c). Compiled out of production:
// only a build with VITE_INPUT_TRACE=1 contains the recorder and its endpoint.
// Pair with the backend env HERDRWEB_DEBUG_INPUT=1 (GET /api/debug/input reads it back).
/** Build-time flag: false in production, so every `if (TRACE) trace(...)` is dead code. */
export const TRACE = import.meta.env.VITE_INPUT_TRACE === "1";

export type Trace = (ev: string, extra?: Record<string, unknown>) => void;

const noop: Trace = () => {};

function makeTrace(ta: HTMLTextAreaElement | undefined | null): Trace & { dispose: () => void } {
  let buf: object[] = [], timer = 0;
  const trace: Trace = (ev, extra = {}) => {
    buf.push({ t: Math.round(performance.now()), ev, ...extra,
      ss: ta?.selectionStart, se: ta?.selectionEnd, tail: ta ? ta.value.slice(-60) : null });
    if (buf.length > 400) buf = buf.slice(-400);
    if (!timer) timer = window.setTimeout(() => {
      timer = 0;
      const batch = buf; buf = [];
      void fetch("/api/debug/input", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ events: batch }) }).catch(() => {});
    }, 1000);
  };
  return Object.assign(trace, { dispose: () => clearTimeout(timer) });
}

export function createTrace(ta: HTMLTextAreaElement | undefined | null): Trace & { dispose: () => void } {
  if (TRACE) return makeTrace(ta);
  return Object.assign(noop, { dispose: () => {} });
}
