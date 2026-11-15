// Fake clock / timer / api for submitDictation (locked acceptance helper).
import { type MicSubmitApi, type MicSubmitDeps, type MicSubmitResult, submitDictation } from "../../../src/components/micSubmit";

type Status = Awaited<ReturnType<MicSubmitApi["status"]>>;

export interface MicRig {
  deps: MicSubmitDeps;
  calls: string[];
  toasts: { message: string; bad?: boolean }[];
  timers: { fn: () => void; ms: number; at: number; id: number; cleared: boolean }[];
  setNow: (t: number) => void;
  now: () => number;
}

/** `statuses` is consumed one per status() call: a value, null (pane gone) or "reject". */
export function micRig(statuses: (Status | "reject")[]): MicRig {
  let t = 10_000, nextId = 1;
  const calls: string[] = [];
  const toasts: MicRig["toasts"] = [];
  const timers: MicRig["timers"] = [];
  const queue = [...statuses];
  const api: MicSubmitApi = {
    deliver: async (pane, text) => { calls.push(`deliver ${pane} ${text}`); },
    sendEnter: async (pane) => { calls.push(`enter ${pane}`); },
    status: async (pane) => {
      calls.push(`status ${pane}`);
      const s = queue.length > 1 ? queue.shift()! : queue[0];
      if (s === "reject") throw new Error("status fetch failed");
      return s as Status;
    },
    toast: (message, bad) => { toasts.push({ message, bad }); },
  };
  const deps: MicSubmitDeps = {
    now: () => t,
    setTimer: (fn, ms) => { const id = nextId++; timers.push({ fn, ms, at: t, id, cleared: false }); return id; },
    clearTimer: (id) => { const x = timers.find((y) => y.id === id); if (x) x.cleared = true; },
    api,
  };
  return { deps, calls, toasts, timers, setNow: (n) => { t = n; }, now: () => t };
}

export const settle = async () => { for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 0)); };

/** Start a submission, let it reach its timer, fire the timer `lateMs` after its due time. */
export async function runSubmit(rig: MicRig, lateMs: number, pane = "w1:p1", text = "hello there"): Promise<MicSubmitResult> {
  const p = submitDictation(rig.deps, pane, text);
  await settle();
  const live = rig.timers.filter((x) => !x.cleared);
  if (live.length) {
    const tm = live[live.length - 1];
    rig.setNow(tm.at + tm.ms + lateMs);
    tm.fn();
  }
  await settle();
  return p;
}
