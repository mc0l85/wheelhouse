// Sticky Ctrl/Alt state machine (H0 skeleton; implemented in lane C). No DOM; clock injected.
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
  reset: () => void;
}

export interface ModifierDeps {
  now: () => number;
  /** Fired whenever either modifier's state changes (including a "once" consumed). */
  onChange: (m: ModName, s: ModState) => void;
}

export function createModifiers(_deps: ModifierDeps): ModifierMachine {
  throw new Error("todo");
}
