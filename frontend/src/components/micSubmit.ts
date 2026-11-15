// Post-dictation submit sequence: deliver, wait, re-check, Enter.
// Clock, timer and api are injected so the whole sequence is testable.

/** Delay between delivering the dictated text and sending Enter. */
export const SUBMIT_MS = 10_000;
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
  | { entered: false; reason: SkipReason | "not-delivered" | "cancelled" };

export interface MicSubmitOptions {
  /** Called once the text is delivered and the countdown starts; `until` is the due time (deps.now clock). */
  onCountdown?: (until: number) => void;
  /** Aborting during the countdown cancels the Enter (text stays in the pane). */
  signal?: { aborted: boolean; addEventListener: (t: "abort", fn: () => void) => void };
}

const WHY: Record<SkipReason, string> = {
  late: "Enter skipped (timer ran late) — text left in the box",
  blocked: "Enter skipped (agent is waiting on a question) — text left in the box",
  gone: "Enter skipped (pane is gone)",
  "status-failed": "Enter skipped (could not check the pane) — text left in the box",
};

export async function submitDictation(
  deps: MicSubmitDeps, paneId: string, text: string, opts: MicSubmitOptions = {},
): Promise<MicSubmitResult> {
  const { api } = deps;
  // Deliver only to a pane that is not waiting on a question. A failed fetch
  // does not stop the (harmless) text; the Enter check below decides.
  try {
    const st = await api.status(paneId);
    if (st === null) { api.toast("pane is gone — nothing sent", true); return { entered: false, reason: "not-delivered" }; }
    if (st === "blocked") { api.toast("agent is waiting on a question — nothing sent", true); return { entered: false, reason: "not-delivered" }; }
  } catch { /* checked again before Enter */ }
  try { await api.deliver(paneId, text); }
  catch (e) {
    api.toast(`send failed: ${(e as Error).message}`, true);
    return { entered: false, reason: "not-delivered" };
  }

  const until = deps.now() + SUBMIT_MS;
  opts.onCountdown?.(until);
  const fired = await new Promise<boolean>((resolve) => {
    const id = deps.setTimer(() => resolve(true), SUBMIT_MS);
    opts.signal?.addEventListener("abort", () => { deps.clearTimer(id); resolve(false); });
    if (opts.signal?.aborted) { deps.clearTimer(id); resolve(false); }
  });
  if (!fired) return { entered: false, reason: "cancelled" };

  const skip = (reason: SkipReason): MicSubmitResult => { api.toast(WHY[reason], true); return { entered: false, reason }; };
  if (deps.now() - until > MAX_LATE_MS) return skip("late");
  let st: Awaited<ReturnType<MicSubmitApi["status"]>>;
  try { st = await api.status(paneId); } catch { return skip("status-failed"); }
  if (st === null) return skip("gone");
  if (st === "blocked") return skip("blocked");
  try { await api.sendEnter(paneId); }
  catch (e) { api.toast(`Enter failed: ${(e as Error).message}`, true); return { entered: false, reason: "status-failed" }; }
  return { entered: true };
}


