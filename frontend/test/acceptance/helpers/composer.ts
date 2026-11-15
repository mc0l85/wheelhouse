// Driver for the pure composer reducer (locked acceptance helper).
//
// Event snapshots follow real browser order:
//   compositionupdate carries the textarea BEFORE the update lands (browsers update the DOM after
//   the event); the `input` (insertCompositionText) right after it carries the textarea AFTER.
//   xterm 5.5's commit onData (origin "commit") is textarea.value.substring(<length at
//   compositionstart>), sent from a setTimeout(0) after compositionend.
// Timers: a setTimer(id, ms) effect arms a timer due at event.now + ms; it fires (as a
// {type:"timeout"} event with now = its due time) before the first later event whose now >= due,
// and every remaining timer fires at the end. cancelTimer disarms; setTimer with a live id re-arms.
// The state handed to reduce() is deep-frozen: the reducer must be pure.
import {
  initialComposerState, reduce,
  type ComposerEffect, type ComposerEvent, type ComposerState, type DataOrigin, type ModSnapshot, type TextareaSnapshot,
} from "../../../src/input/composer";

export const OFF: ModSnapshot = { ctrl: "off", alt: "off" };

export function ta(value: string, selStart?: number, selEnd?: number): TextareaSnapshot {
  const s = selStart ?? value.length;
  return { value, selStart: s, selEnd: selEnd ?? s };
}

export const ev = {
  start: (now: number, t: TextareaSnapshot, mods: ModSnapshot = OFF): ComposerEvent =>
    ({ type: "compositionstart", now, ta: t, mods }),
  update: (now: number, data: string, before: TextareaSnapshot, mods: ModSnapshot = OFF): ComposerEvent =>
    ({ type: "compositionupdate", now, data, ta: before, mods }),
  end: (now: number, data: string, t: TextareaSnapshot): ComposerEvent =>
    ({ type: "compositionend", now, data, ta: t }),
  before: (now: number, inputType: string, data: string | null, t: TextareaSnapshot, mods: ModSnapshot = OFF): ComposerEvent =>
    ({ type: "beforeinput", now, inputType, data, ta: t, mods }),
  input: (now: number, inputType: string, t: TextareaSnapshot): ComposerEvent =>
    ({ type: "input", now, inputType, ta: t }),
  data: (now: number, data: string, origin: DataOrigin): ComposerEvent =>
    ({ type: "onData", now, data, origin }),
  sendFailed: (now: number): ComposerEvent => ({ type: "sendFailed", now }),
  reset: (now: number): ComposerEvent => ({ type: "reset", now }),
};

function deepFreeze<T>(o: T): T {
  if (o && typeof o === "object" && !Object.isFrozen(o)) {
    Object.freeze(o);
    for (const v of Object.values(o as object)) deepFreeze(v);
  }
  return o;
}

export interface Step { event: ComposerEvent; send: string; effects: ComposerEffect[] }
export interface Run { send: string; steps: Step[]; effects: ComposerEffect[]; state: ComposerState }

export function drive(events: ComposerEvent[], init?: ComposerState): Run {
  let state = init ?? initialComposerState();
  const timers = new Map<string, number>();
  const steps: Step[] = [];
  const apply = (event: ComposerEvent) => {
    const r = reduce(deepFreeze(state), event);
    state = r.state;
    steps.push({ event, send: r.send, effects: r.effects });
    for (const e of r.effects) {
      if (e.type === "setTimer") timers.set(e.id, event.now + e.ms);
      else if (e.type === "cancelTimer") timers.delete(e.id);
    }
  };
  const fireDue = (upTo: number) => {
    for (;;) {
      let next: [string, number] | null = null;
      for (const t of timers) if (t[1] <= upTo && (!next || t[1] < next[1])) next = t;
      if (!next) return;
      timers.delete(next[0]);
      apply({ type: "timeout", now: next[1], id: next[0] });
    }
  };
  for (const e of events) { fireDue(e.now); apply(e); }
  fireDue(Number.POSITIVE_INFINITY);
  return { send: steps.map((s) => s.send).join(""), steps, effects: steps.flatMap((s) => s.effects), state };
}

/** Bytes sent after the first step matching `pred` (exclusive). */
export function sentAfter(run: Run, pred: (e: ComposerEvent) => boolean): string {
  const i = run.steps.findIndex((s) => pred(s.event));
  return i < 0 ? "" : run.steps.slice(i + 1).map((s) => s.send).join("");
}

export function hex(s: string): string {
  return Array.from(new TextEncoder().encode(s), (b) => b.toString(16).padStart(2, "0")).join(" ");
}

/** What the pane shows: DEL (0x7f) deletes one grapheme cluster (Decision 9). */
export function applyDel(s: string): string {
  const Seg = (Intl as unknown as { Segmenter: new (l?: string, o?: { granularity: string }) =>
    { segment: (s: string) => Iterable<{ segment: string }> } }).Segmenter;
  const seg = new Seg(undefined, { granularity: "grapheme" });
  const out: string[] = [];
  let run = "";
  const flush = () => { for (const g of seg.segment(run)) out.push(g.segment); run = ""; };
  for (const ch of s) {
    if (ch === "\x7f") { flush(); out.pop(); } else run += ch;
  }
  flush();
  return out.join("");
}

/** One composed word in iOS predictive / Gboard style, typed letter by letter at the end of `base`.
 *  Returns the events from compositionstart to compositionend (no commit onData). */
export function composeWord(t0: number, base: string, word: string, step = 60): { events: ComposerEvent[]; t: number } {
  const events: ComposerEvent[] = [ev.start(t0, ta(base))];
  let cur = "", t = t0;
  for (const ch of word) {
    t += step;
    events.push(ev.update(t, cur + ch, ta(base + cur)));
    cur += ch;
    events.push(ev.input(t + 1, "insertCompositionText", ta(base + cur)));
  }
  t += step;
  events.push(ev.end(t, word, ta(base + word)));
  return { events, t };
}
