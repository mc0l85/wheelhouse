// Reconnect policy for the terminal socket. Pure: terminalSocket feeds it every
// close and does what it says.

/** A handshake not open within this long is abandoned and retried. */
export const CONNECT_TIMEOUT_MS = 10000;

/** From this many consecutive closes of sockets that never opened, a message is shown. */
export const NEVER_OPENED_LIMIT = 3;

/** Longest wait between attempts. */
export const MAX_BACKOFF_MS = 30000;
/** Shortest wait after the server refused us (close 1008 / 1013 / 4503 / 4500). */
const REFUSAL_MIN_MS = 2000;

export interface CloseDecision {
  retry: boolean;
  delayMs: number;
  /** Shown to the user (1008, 1013, 4503, 4500 and "N closes without opening"); includes the close reason when given. */
  message?: string;
}

const REFUSALS: Record<number, string> = {
  1008: "connection refused",
  1013: "server busy",
  4503: "herdr server not running",
  4500: "herdr binary missing",
};

/**
 * @param everOpened  this socket reached OPEN before it closed
 * @param failStreak  consecutive closes of sockets that never opened, counting this one (0 when this one opened)
 */
export function onClose(code: number, reason: string, everOpened: boolean, failStreak: number): CloseDecision {
  const streak = everOpened ? 0 : Math.max(1, failStreak);
  // Quick first retry after a drop; doubling for each socket that never opened.
  let delayMs = everOpened ? 250 : Math.min(250 * 2 ** streak, MAX_BACKOFF_MS);
  let message: string | undefined;
  const label = REFUSALS[code];
  const why = (reason ?? "").trim();
  if (label !== undefined) {
    message = why ? `${label}: ${why}` : label;
    delayMs = Math.max(delayMs, REFUSAL_MIN_MS);
  } else if (!everOpened && streak >= NEVER_OPENED_LIMIT) {
    message = `cannot reach herdr (${streak} attempts without connecting)${why ? `: ${why}` : ""}`;
  }
  return { retry: true, delayMs, message };
}
