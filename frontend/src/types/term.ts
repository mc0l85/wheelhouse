// Frozen terminal types shared by the hook, the socket module and the key bar.

// Sticky modifier state: off, armed for the next key, or locked.
export type ModState = "off" | "once" | "locked";

export interface TermApi {
  type: (s: string) => void;          // raw text/sequence
  ctrlCombo: (letter: string) => void; // e.g. "c" → ^C
  toggleCtrl: () => ModState;         // returns the new modifier state
  toggleAlt: () => ModState;
  focus: () => void;                  // pop the soft keyboard
  paste: (text: string) => void;      // bracketed paste into the PTY
  armed: () => { ctrl: ModState; alt: ModState };
  scrollToBottom: (rect: { x: number; y: number; width: number; height: number }, lines: number) => void;
  fontStep: (delta: number) => number;  // ±px, clamped + remembered; returns new size
  fontSize: () => number;
  redraw: () => void;                  // save a glitch report, reconnect = full repaint
}

export type ConnState = "connecting" | "open" | "reconnecting" | "restored";

// Connection indicator payload: the state plus an optional human message.
export interface ConnInfo {
  state: ConnState;
  message?: string;
}
