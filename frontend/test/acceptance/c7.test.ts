// C-7 regression: traces that work today stay byte-identical (iOS predictive, Gboard, hardware).
import { describe, expect, it } from "vitest";
import type { ComposerEvent } from "../../src/input/composer";
import { composeWord, drive, ev, hex, ta } from "./helpers/composer";

describe("C-7 unchanged traces", () => {
  it("iOS predictive: composed words, the space typed as insertText", () => {
    // per word: compositionstart … compositionend, xterm commit onData(word) right after,
    // then beforeinput/input insertText " " and xterm onData(" ", other)
    const events: ComposerEvent[] = [];
    let base = "", t = 0;
    for (const w of ["hi", "there"]) {
      const c = composeWord(t, base, w);
      events.push(...c.events, ev.data(c.t + 1, w, "commit"));
      t = c.t + 400;
      events.push(ev.before(t, "insertText", " ", ta(base + w)));
      events.push(ev.input(t + 1, "insertText", ta(base + w + " ")));
      events.push(ev.data(t + 2, " ", "other"));
      base += w + " "; t += 300;
    }
    expect(hex(drive(events).send)).toBe(hex("hi there "));
  });

  it("Gboard: composed words, the space arrives as a textarea change after the commit", () => {
    // per word: compositionstart … compositionend, xterm commit onData(word),
    // the space lands in the textarea, xterm's textarea-change handler sends onData(" ", textareaChange)
    const events: ComposerEvent[] = [];
    let base = "", t = 0;
    for (const w of ["hi", "there"]) {
      const c = composeWord(t, base, w, 90);
      events.push(...c.events, ev.data(c.t + 1, w, "commit"));
      t = c.t + 250;
      events.push(ev.input(t, "insertText", ta(base + w + " ")));
      events.push(ev.data(t + 1, " ", "textareaChange"));
      base += w + " "; t += 300;
    }
    expect(hex(drive(events).send)).toBe(hex("hi there "));
  });

  it("hardware keyboard: one onData per key", () => {
    const events = Array.from("hello world", (ch, i) => ev.data(i * 30, ch, "other"));
    expect(hex(drive(events).send)).toBe(hex("hello world"));
  });

  it("hardware Enter and Backspace pass through", () => {
    const events = Array.from("ls\x7fs\r", (ch, i) => ev.data(i * 30, ch, "other"));
    expect(hex(drive(events).send)).toBe(hex("ls\x7fs\r"));
  });
});
