// Post-dictation submit sequence (H0 skeleton; implemented in lane D).
// Clock, timer and api are injected so the whole sequence is testable.

/** Delay between delivering the dictated text and sending Enter. */
export const SUBMIT_MS = 1500;
/** A timer firing later than this after its due time skips Enter. */
export const MAX_LATE_MS = 1500;

export type SkipReason = "late" | "blocked" | "gone" | "status-failed";

export interface MicSubmitApi {
  /** Deliver text to the target pane; resolves when sent. */
  deliver: (paneId: string, text: string) => Promise<void>;
  sendEnter: (paneId: string) => Promise<void>;
  /** Current agent status of the pane; null = pane gone; rejects on fetch failure. */
  status: (paneId: string) => Promise<"blocked" | "working" | "idle" | "done" | "unknown" | null>;
  toast: (message: string, bad?: boolean) => void;
}

export interface MicSubmitDeps {
  now: () => number;
  setTimer: (fn: () => void, ms: number) => number;
  clearTimer: (id: number) => void;
  api: MicSubmitApi;
}

export type MicSubmitResult =
  | { entered: true }
  | { entered: false; reason: SkipReason | "not-delivered" };

export function submitDictation(_deps: MicSubmitDeps, _paneId: string, _text: string): Promise<MicSubmitResult> {
  throw new Error("todo");
}
