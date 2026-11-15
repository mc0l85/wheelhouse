// Reconnect policy for the terminal socket (H0 skeleton; implemented in lane D).

/** A handshake not open within this long is abandoned and retried. */
export const CONNECT_TIMEOUT_MS = 10000;

/** From this many consecutive closes of sockets that never opened, a message is shown. */
export const NEVER_OPENED_LIMIT = 3;

export interface CloseDecision {
  retry: boolean;
  delayMs: number;
  /** Shown to the user (1008, 1013, 4503, 4500 and "N closes without opening"); includes the close reason when given. */
  message?: string;
}

/**
 * @param everOpened  this socket reached OPEN before it closed
 * @param failStreak  consecutive closes of sockets that never opened, counting this one (0 when this one opened)
 */
export function onClose(_code: number, _reason: string, _everOpened: boolean, _failStreak: number): CloseDecision {
  throw new Error("todo");
}
