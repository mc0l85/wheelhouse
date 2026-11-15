// Sticky Ctrl/Alt state machine. No DOM; clock injected.
import type { ModState } from "../types/term";

export type ModSource = "key" | "composition" | "tray" | "paste" | "report";
export type ModName = "ctrl" | "alt";

/** A second tap within this window locks the modifier. */
export const DOUBLE_TAP_MS = 400;

export interface ModifierMachine {
  state: (m: ModName) => ModState;
  /** A tap on the Ctrl/Alt key; returns the new state. */
  tap: (m: ModName) => ModState;
  /** Transform outgoing data per the active modifier; may consume a "once". */
  apply: (data: string, source: ModSource) => string;
  /** Consume a "once" modifier without transforming anything (the composer already applied it). */
  consume: (m: ModName) => void;
  reset: () => void;
}

export interface ModifierDeps {
  now: () => number;
  /** Fired whenever either modifier's state changes (including a "once" consumed). */
  onChange: (m: ModName, s: ModState) => void;
}

const CTRL_PUNCT: Record<string, string> = {
  " ": "\x00", "@": "\x00", "[": "\x1b", "\\": "\x1c", "]": "\x1d", "^": "\x1e", "_": "\x1f", "?": "\x7f",
};

/** Ctrl translation of one character, or null when it has none. */
export function ctrlChar(ch: string): string | null {
  const c = ch.toLowerCase().charCodeAt(0);
  if (ch.length === 1 && c >= 97 && c <= 122) return String.fromCharCode(c - 96);
  return CTRL_PUNCT[ch] ?? null;
}

/** One printable code point: not a C0/DEL/C1 control. */
function isPrintable(ch: string): boolean {
  const cp = ch.codePointAt(0) ?? 0;
  return cp >= 0x20 && cp !== 0x7f && !(cp >= 0x80 && cp <= 0x9f);
}

export interface ModApplied { out: string; usedCtrl: boolean; usedAlt: boolean }

/** Pure transform of `data` (one unit: a single code point) under the given modifier states.
 *  `used*` say the modifier was spent on this char (applied or an unmapped printable). */
export function applyMods(data: string, ctrl: ModState, alt: ModState, source: ModSource): ModApplied {
  const none = { out: data, usedCtrl: false, usedAlt: false };
  if (source === "paste" || source === "report") return none;
  if (ctrl === "off" && alt === "off") return none;
  const chars = Array.from(data);
  if (chars.length !== 1) return none;
  const ch = chars[0];
  let out = ch, usedCtrl = false, usedAlt = false;
  if (ctrl !== "off" && isPrintable(ch)) {
    usedCtrl = true;
    out = ctrlChar(ch) ?? ch;   // unmapped: unmodified, modifier still consumed
  }
  if (alt !== "off" && (isPrintable(ch) || ch === "\r" || ch === "\t" || ch === "\x7f")) {
    usedAlt = true;
    out = "\x1b" + out;
  }
  return { out, usedCtrl, usedAlt };
}

export function createModifiers(deps: ModifierDeps): ModifierMachine {
  const st: Record<ModName, ModState> = { ctrl: "off", alt: "off" };
  const tappedAt: Record<ModName, number> = { ctrl: 0, alt: 0 };
  const set = (m: ModName, s: ModState) => {
    if (st[m] === s) return;
    st[m] = s;
    deps.onChange(m, s);
  };
  const tap = (m: ModName): ModState => {
    const now = deps.now();
    if (st[m] === "off") { tappedAt[m] = now; set(m, "once"); }
    else if (st[m] === "once" && now - tappedAt[m] <= DOUBLE_TAP_MS) set(m, "locked");
    else set(m, "off");
    return st[m];
  };
  const apply = (data: string, source: ModSource): string => {
    const r = applyMods(data, st.ctrl, st.alt, source);
    if (r.usedCtrl && st.ctrl === "once") set("ctrl", "off");
    if (r.usedAlt && st.alt === "once") set("alt", "off");
    return r.out;
  };
  const consume = (m: ModName) => { if (st[m] === "once") set(m, "off"); };
  const reset = () => { set("ctrl", "off"); set("alt", "off"); };
  return { state: (m) => st[m], tap, apply, consume, reset };
}
