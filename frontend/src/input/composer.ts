// IME / dictation / autocorrect composer
// Pure reducer: no DOM; clock and timers are injected through events and effects.
import type { ModState } from "../types/term";
import { applyMods } from "./modifiers";

/** xterm's commit onData arriving this long after compositionend may still be the duplicate of a
 *  composition already sent live (dropped once, PRD 4.1); later, an identical onData is always sent. */
export const DUP_WINDOW_MS = 1500;

/** An IME keydown (keyCode 229 / isComposing) is not a hardware key: Safari sends one right after compositionend. */
export function isImeKeydown(e: { keyCode: number; isComposing?: boolean }): boolean {
  return e.keyCode === 229 || e.isComposing === true;
}

export interface TextareaSnapshot { value: string; selStart: number; selEnd: number }

export type DataOrigin = "commit" | "keydownFinalize" | "textareaChange" | "other";

/** Modifier state at the time of the event, so a composing keyboard's first letter can be modified. */
export interface ModSnapshot { ctrl: ModState; alt: ModState }

export type ComposerEvent =
  | { type: "compositionstart"; now: number; ta: TextareaSnapshot; mods: ModSnapshot }
  | { type: "compositionupdate"; now: number; data: string; ta: TextareaSnapshot; mods: ModSnapshot }
  | { type: "compositionend"; now: number; data: string; ta: TextareaSnapshot }
  | { type: "beforeinput"; now: number; inputType: string; data: string | null; ta: TextareaSnapshot; mods: ModSnapshot }
  | { type: "input"; now: number; inputType: string; ta: TextareaSnapshot }
  | { type: "onData"; now: number; data: string; origin: DataOrigin }
  | { type: "timeout"; now: number; id: string }
  | { type: "sendFailed"; now: number }
  | { type: "reset"; now: number }
  | { type: "blur"; now: number };

export type ComposerEffect =
  | { type: "setTimer"; id: string; ms: number }
  | { type: "cancelTimer"; id: string }
  | { type: "clearTextarea" }
  | { type: "consumeModifier"; m: "ctrl" | "alt" }
  /** Tell the user something was not sent. */
  | { type: "toast"; msg: string };

export interface ComposerState {
  /** Composed text the pane already has (null = not composing). */
  comp: string | null;
  compFinal: string;
  lastCommit: string;
  lastCommitAt: number;
  replPending: { before: string; s0: number; e0: number } | null;
  /** Modifier state at the start of the current composition. */
  ctrl: ModState;
  alt: ModState;
  /** The commit timer is armed: composition ended, xterm's commit onData may still arrive. */
  timerArmed: boolean;
  /** What xterm's commit onData carries for the current/last composition (textarea tail from its start). */
  expected: string;
  /** `expected` of the composition that armed `lastCommit`. */
  lastExpected: string;
  startLen: number;
  /** Textarea value at compositionstart when its cursor was collapsed at the end (else null). */
  startVal: string | null;
  /** The composition's first letter went out modified: the pane has `sent` where the text has `raw`. */
  lead: { raw: string; sent: string } | null;
  /** First update whose text already ends the textarea: a re-composition of an existing word, or new text? */
  adopt: { data: string; len: number; mods: ModSnapshot } | null;
  /** xterm's onData for an insertText we already sent (drop it once). */
  suppress: string | null;
  /** Clear the textarea after the next input event (a replacement we could not map onto the pane). */
  clearOnInput: boolean;
  /** A socket reset abandoned the composition in progress: send nothing more for it until the next compositionstart. */
  abandoned: "live" | "ended" | null;
  /** The texts the abandoned composition could still commit (xterm's commit / flush is swallowed only if it matches). */
  abandonedText: string[];
  /** Between compositionstart and compositionend (survives sendFailed, which forgets `comp`). */
  inComp: boolean;
}

export interface ComposerResult {
  state: ComposerState;
  send: string;
  effects: ComposerEffect[];
}

export function initialComposerState(): ComposerState {
  return {
    comp: null, compFinal: "", lastCommit: "", lastCommitAt: 0, replPending: null,
    ctrl: "off", alt: "off", timerArmed: false, expected: "", lastExpected: "", startLen: 0, startVal: null,
    lead: null, adopt: null, suppress: null, clearOnInput: false, abandoned: null, abandonedText: [], inComp: false,
  };
}

const COMMIT_TIMER = "commit";
const COMMIT_WAIT_MS = 150;
const SUPPRESS_TIMER = "suppress";
const ABANDON_TIMER = "abandon";
const SUPPRESS_MS = 500;

type Segmenter = { segment: (s: string) => Iterable<{ segment: string }> };
let segmenter: Segmenter | null | undefined;
function graphemes(s: string): string[] {
  if (segmenter === undefined) {
    const Seg = (Intl as unknown as { Segmenter?: new (l?: string, o?: { granularity: string }) => Segmenter }).Segmenter;
    segmenter = Seg ? new Seg(undefined, { granularity: "grapheme" }) : null;
  }
  if (!segmenter) return Array.from(s);
  return Array.from(segmenter.segment(s), (x) => x.segment);
}

/** Prefix-minimal edit: one DEL per grapheme after the common grapheme prefix, then the new suffix. */
function diff(from: string, to: string): string {
  if (from === to) return "";
  const a = graphemes(from), b = graphemes(to);
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i++;
  return "\x7f".repeat(a.length - i) + b.slice(i).join("");
}

/** diff() over composed text whose first letter may have gone out modified. */
function diffComp(s: ComposerState, from: string, to: string): { send: string; lead: ComposerState["lead"] } {
  const lead = s.lead;
  if (lead && from.startsWith(lead.raw)) {
    if (to.startsWith(lead.raw)) return { send: diff(from.slice(lead.raw.length), to.slice(lead.raw.length)), lead };
    return { send: diff(from.slice(lead.raw.length), to), lead: null };   // cannot un-send the modified letter
  }
  return { send: diff(from, to), lead: null };
}

function tailSel(t: TextareaSnapshot): string {
  return t.selStart < t.selEnd && t.selEnd === t.value.length ? t.value.slice(t.selStart, t.selEnd) : "";
}

function longestPrefix(d: string, cands: string[]): string | null {
  let best: string | null = null;
  for (const c of cands) if (c && d.startsWith(c) && (best === null || c.length > best.length)) best = c;
  return best;
}

/** The composition's first letter under the modifier state: sends it modified, the rest plain. */
function firstSend(s: ComposerState, data: string, mods: ModSnapshot, effects: ComposerEffect[]): { state: ComposerState; send: string } {
  const first = Array.from(data)[0];
  const r = applyMods(first, mods.ctrl, mods.alt, "composition");
  const used = r.usedCtrl || r.usedAlt;
  if (r.usedCtrl && mods.ctrl === "once") effects.push({ type: "consumeModifier", m: "ctrl" });
  if (r.usedAlt && mods.alt === "once") effects.push({ type: "consumeModifier", m: "alt" });
  return {
    state: { ...s, comp: data, lead: used ? { raw: first, sent: r.out } : null, ctrl: mods.ctrl, alt: mods.alt },
    send: r.out + data.slice(first.length),
  };
}

/** A pending re-composition adopt whose textarea did not grow is settled as an adopt (no `input` needed). */
function settleAdopt(s: ComposerState, ta: TextareaSnapshot): ComposerState {
  if (s.adopt && ta.value.length === s.adopt.len) return { ...s, comp: s.adopt.data, adopt: null };
  return s;
}

function resolveAdopt(s: ComposerState, effects: ComposerEffect[]): { state: ComposerState; send: string } {
  if (!s.adopt) return { state: s, send: "" };
  const a = s.adopt;
  return firstSend({ ...s, adopt: null, comp: "" }, a.data, a.mods, effects);
}

/** Composition over: put the pane at `compFinal`; `arm` keeps the duplicate-commit guard for a late xterm commit. */
function finishComp(s: ComposerState, now: number, arm: boolean, effects: ComposerEffect[]): { state: ComposerState; send: string } {
  let send = "";
  if (s.comp !== null) { const d = diffComp(s, s.comp, s.compFinal); send = d.send; }
  if (s.timerArmed) effects.push({ type: "cancelTimer", id: COMMIT_TIMER });
  const keep = arm && s.compFinal !== "";
  return {
    state: {
      ...s, comp: null, lead: null, timerArmed: false,
      lastCommit: keep ? s.compFinal : "", lastExpected: keep ? s.expected : "", lastCommitAt: keep ? now : s.lastCommitAt,
    },
    send,
  };
}

export function reduce(state: ComposerState, event: ComposerEvent): ComposerResult {
  const effects: ComposerEffect[] = [];
  const done = (st: ComposerState, send = ""): ComposerResult => ({ state: st, send, effects });
  let s = state;

  switch (event.type) {
    case "compositionstart": {
      let send = "";
      if (s.timerArmed) { const r = finishComp(s, event.now, false, effects); s = r.state; send = r.send; }
      if (s.suppress !== null) effects.push({ type: "cancelTimer", id: SUPPRESS_TIMER });
      if (s.abandoned) effects.push({ type: "cancelTimer", id: ABANDON_TIMER });
      return done({
        ...s, comp: tailSel(event.ta), lead: null, adopt: null, suppress: null, replPending: null,
        ctrl: event.mods.ctrl, alt: event.mods.alt, startLen: event.ta.value.length,
        startVal: event.ta.selStart === event.ta.selEnd && event.ta.selEnd === event.ta.value.length ? event.ta.value : null,
        lastCommit: "", lastExpected: "", expected: "", clearOnInput: false, abandoned: null, abandonedText: [], inComp: true,
      }, send);
    }

    case "compositionupdate": {
      if (s.abandoned) return done(event.data ? { ...s, abandonedText: [...s.abandonedText, event.data] } : s);
      let send = "";
      s = settleAdopt(s, event.ta);
      if (s.adopt) { const r = resolveAdopt(s, effects); s = r.state; send = r.send; }
      const data = event.data;
      if (s.comp === null) s = { ...s, comp: "", lead: null };
      if (s.comp === "" && data !== "") {
        // Judged on the textarea as it was at compositionstart (the update's own snapshot may
        // already include the new text, depending on the browser's event order).
        if (s.startVal !== null && s.startVal.endsWith(data)) {
          // Re-composition of a word the pane already has, or fresh text that happens to match
          // the textarea's tail? The `input` that follows tells (the textarea grows or not).
          return done({ ...s, adopt: { data, len: s.startLen, mods: event.mods } }, send);
        }
        const r = firstSend(s, data, event.mods, effects);
        return done(r.state, send + r.send);
      }
      const d = diffComp(s, s.comp ?? "", data);
      return done({ ...s, comp: data, lead: d.lead }, send + d.send);
    }

    case "compositionend": {
      if (s.abandoned) {
        // xterm's commit for it may follow; stop expecting one when the commit window is over.
        effects.push({ type: "setTimer", id: ABANDON_TIMER, ms: DUP_WINDOW_MS });
        return done({ ...s, abandoned: "ended", abandonedText: event.data ? [...s.abandonedText, event.data] : s.abandonedText });
      }
      let send = "";
      s = settleAdopt(s, event.ta);
      if (s.adopt) { const r = resolveAdopt(s, effects); s = r.state; send = r.send; }
      if (s.comp === null) return done({ ...s, inComp: false }, send);   // already finalized (keydown) or reset: nothing to settle
      const compFinal = event.data;
      effects.push({ type: "setTimer", id: COMMIT_TIMER, ms: COMMIT_WAIT_MS });
      return done({ ...s, compFinal, timerArmed: true, inComp: false, expected: event.ta.value.substring(s.startLen) }, send);
    }

    case "beforeinput": {
      if (event.inputType !== "insertText" && event.inputType !== "insertReplacementText") return done(s);
      const t = event.ta;
      if (t.selStart >= t.selEnd) {
        // Nothing selected. An autocorrect tap with a collapsed caret still replaces text, but
        // getTargetRanges() is empty for a textarea: settle it from the textarea before/after.
        if (event.inputType === "insertReplacementText")
          return done({ ...s, replPending: { before: t.value, s0: t.selStart, e0: t.selEnd } });
        return done(s);
      }
      const insertText = event.inputType === "insertText";
      if (s.suppress !== null) effects.push({ type: "cancelTimer", id: SUPPRESS_TIMER });
      s = { ...s, suppress: null };
      if (t.selEnd !== t.value.length) {
        // Not at the end: cannot be mapped onto the pane's cursor. Send nothing, clear the textarea.
        if (insertText && event.data) {
          effects.push({ type: "setTimer", id: SUPPRESS_TIMER, ms: SUPPRESS_MS });
          s = { ...s, suppress: event.data };
        }
        effects.push({ type: "toast", msg: "edit not sent: dictation changed earlier text" });
        return done({ ...s, clearOnInput: true });
      }
      const gone = t.value.slice(t.selStart, t.selEnd);
      if (!insertText) return done({ ...s, replPending: { before: t.value, s0: t.selStart, e0: t.selEnd } });
      const data = event.data ?? "";
      if (data) {
        effects.push({ type: "setTimer", id: SUPPRESS_TIMER, ms: SUPPRESS_MS });
        s = { ...s, suppress: data };
      }
      return done(s, diff(gone, data));
    }

    case "input": {
      let send = "";
      if (s.adopt) {
        if (event.ta.value.length === s.adopt.len) s = { ...s, comp: s.adopt.data, adopt: null };
        else { const r = resolveAdopt(s, effects); s = r.state; send = r.send; }
      }
      if (s.replPending && event.inputType === "insertReplacementText") {
        const { before, s0, e0 } = s.replPending;
        const v = event.ta.value;
        if (s0 >= e0) {
          if (s0 === before.length) send += diff(before, v);    // caret was at the end: a tail replacement
          else {
            effects.push({ type: "toast", msg: "edit not sent: autocorrect changed earlier text" });
            s = { ...s, clearOnInput: true };
          }
        } else send += diff(before.slice(s0, e0), v.slice(s0, v.length - (before.length - e0)));
        s = { ...s, replPending: null };
      }
      if (s.clearOnInput) { effects.push({ type: "clearTextarea" }); s = { ...s, clearOnInput: false }; }
      return done(s, send);
    }

    case "onData": {
      const d = event.data;
      // Escape sequences (terminal query replies, key reports) are never a composition commit.
      const origin: DataOrigin = event.origin === "commit" && d.startsWith("\x1b") ? "other" : event.origin;
      if (s.abandoned && (origin === "commit" || origin === "keydownFinalize")) {
        // xterm's flush / commit for the abandoned composition: swallow only what matches its text.
        const m = longestPrefix(d, s.abandonedText);
        const part = m === null && d !== "" && s.abandonedText.some((c) => c.startsWith(d));
        if (m !== null || part || d === "") {
          const next = origin === "commit" ? { ...s, abandoned: null, abandonedText: [] } : s;
          return done(next, m !== null ? d.slice(m.length) : "");
        }
        // Unrelated data: send it as ordinary input; the abandonment stands.
        return done(s, d);
      }
      if (origin === "keydownFinalize") {
        if (s.comp === null) return done(s, d);
        // xterm's flush can be empty when it has not yet seen the composed text: never erase the pane for that.
        const r = d === "" ? { send: "" } : diffComp(s, s.comp, d);
        return done({ ...s, comp: null, lead: null, lastCommit: d || s.comp, lastExpected: d || s.comp, lastCommitAt: event.now }, r.send);
      }
      if (s.suppress !== null && d === s.suppress) {
        effects.push({ type: "cancelTimer", id: SUPPRESS_TIMER });
        return done({ ...s, suppress: null });
      }
      if (s.timerArmed) {
        const m = origin === "commit" ? longestPrefix(d, [s.expected, s.compFinal]) : null;
        const r = finishComp(s, event.now, m === null, effects);
        return done(r.state, r.send + (m === null ? d : d.slice(m.length)));
      }
      if (origin === "commit" && s.lastCommit !== "" && event.now - s.lastCommitAt < DUP_WINDOW_MS) {
        const m = longestPrefix(d, [s.lastExpected, s.lastCommit]);
        if (m !== null) return done({ ...s, lastCommit: "", lastExpected: "" }, d.slice(m.length));
      }
      return done(s, d);
    }

    case "timeout": {
      if (event.id === COMMIT_TIMER && s.timerArmed) {
        const r = finishComp(s, event.now, true, effects);
        return done(r.state, r.send);
      }
      if (event.id === SUPPRESS_TIMER) return done({ ...s, suppress: null });
      if (event.id === ABANDON_TIMER) return done(s.abandoned === "ended" ? { ...s, abandoned: null, abandonedText: [] } : s);
      return done(s);
    }

    case "blur": {
      // The textarea lost focus: a composition whose end we never saw is over; the pane keeps its bytes.
      // (An ended composition waiting for xterm's commit is left alone.)
      if (s.timerArmed) return done(s);
      return done({ ...s, comp: null, lead: null, adopt: null, inComp: false, abandoned: s.abandoned === "live" ? null : s.abandoned });
    }

    case "sendFailed":
    case "reset": {
      if (s.timerArmed) effects.push({ type: "cancelTimer", id: COMMIT_TIMER });
      if (s.suppress !== null) effects.push({ type: "cancelTimer", id: SUPPRESS_TIMER });
      // reset: a composition in progress (or just ended, commit pending) is abandoned.
      const abandoned = event.type === "reset" && (s.inComp || s.comp !== null || s.timerArmed || s.adopt !== null)
        ? (s.timerArmed ? "ended" : "live") : null;
      // sendFailed outside a composition: the textarea's tail no longer matches the pane, so a later
      // selected-tail replacement must not erase text that never arrived.
      if (event.type === "sendFailed" && !s.inComp) effects.push({ type: "clearTextarea" });
      // What the abandoned composition could still commit, and an expiry for the "ended" case.
      const abandonedText = abandoned ? [s.expected, s.compFinal, s.comp ?? "", s.adopt?.data ?? ""].filter((x) => x !== "") : [];
      if (abandoned === "ended") effects.push({ type: "setTimer", id: ABANDON_TIMER, ms: DUP_WINDOW_MS });
      return done({ ...initialComposerState(), startLen: s.startLen, abandoned, abandonedText,
        inComp: event.type === "sendFailed" && s.inComp });
    }
  }
}
