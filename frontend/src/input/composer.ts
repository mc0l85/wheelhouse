// IME / dictation / autocorrect composer (H0 skeleton; implemented in lane C).
// Pure reducer: no DOM; clock and timers are injected through events and effects.
import type { ModState } from "../types/term";

/** xterm's commit onData arriving this long after compositionend may still be the duplicate of a
 *  composition already sent live (dropped once, PRD 4.1); later, an identical onData is always sent. */
export const DUP_WINDOW_MS = 1500;

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
  | { type: "reset"; now: number };

export type ComposerEffect =
  | { type: "setTimer"; id: string; ms: number }
  | { type: "cancelTimer"; id: string }
  | { type: "clearTextarea" }
  | { type: "consumeModifier"; m: "ctrl" | "alt" };

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
}

export interface ComposerResult {
  state: ComposerState;
  send: string;
  effects: ComposerEffect[];
}

export function initialComposerState(): ComposerState {
  throw new Error("todo");
}

export function reduce(_state: ComposerState, _event: ComposerEvent): ComposerResult {
  throw new Error("todo");
}
