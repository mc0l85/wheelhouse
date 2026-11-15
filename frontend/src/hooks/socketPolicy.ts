// Reconnect policy for the terminal socket (H0 skeleton; implemented in lane D).

/** A handshake not open within this long is abandoned and retried. */
export const CONNECT_TIMEOUT_MS = 10000;

export interface CloseDecision {
  retry: boolean;
  delayMs: number;
  /** Shown to the user (1008, 1013, 4503, 4500 and "N closes without opening"). */
  message?: string;
}

export function onClose(_code: number, _reason: string, _everOpened: boolean): CloseDecision {
  throw new Error("todo");
}
