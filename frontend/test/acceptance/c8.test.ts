// C-8 (F9a): a commit carrying trailing characters (a non-composition key typed while xterm's commit
// was pending lands in the same commit onData) → only the remainder is sent, whether the commit
// onData arrives before or after the composer's commit timer.
import { describe, expect, it } from "vitest";
import { DUP_WINDOW_MS, type ComposerEvent } from "../../src/input/composer";
import { composeWord, drive, ev, hex, ta } from "./helpers/composer";

function trace(commitAt: (endT: number) => number): ComposerEvent[] {
  // order: compose "hi" (start … end), "2" typed (beforeinput/input insertText, no onData of its own),
  // xterm commit onData("hi2", commit) = textarea.substring(0)
  const c = composeWord(0, "", "hi");
  return [
    ...c.events,
    ev.before(c.t + 2, "insertText", "2", ta("hi")),
    ev.input(c.t + 3, "insertText", ta("hi2")),
    ev.data(commitAt(c.t), "hi2", "commit"),
  ];
}

describe("C-8 commit with trailing chars", () => {
  it("before the commit timer", () => {
    expect(hex(drive(trace((t) => t + 5)).send)).toBe(hex("hi2"));
  });
  it("after the commit timer (commit onData late, inside the duplicate window)", () => {
    expect(DUP_WINDOW_MS).toBeGreaterThan(400);
    expect(hex(drive(trace((t) => t + 400)).send)).toBe(hex("hi2"));
  });
  it("Gboard: the space is part of the commit", () => {
    // order: compose "hi", space lands while the commit is pending, onData("hi ", commit),
    // compose "there" from base "hi ", space, onData("there ", commit) = substring(3)
    const a = composeWord(0, "", "hi", 90);
    const b = composeWord(a.t + 300, "hi ", "there", 90);
    const events: ComposerEvent[] = [
      ...a.events,
      ev.before(a.t + 2, "insertText", " ", ta("hi")),
      ev.input(a.t + 3, "insertText", ta("hi ")),
      ev.data(a.t + 4, "hi ", "commit"),
      ...b.events,
      ev.before(b.t + 2, "insertText", " ", ta("hi there")),
      ev.input(b.t + 3, "insertText", ta("hi there ")),
      ev.data(b.t + 4, "there ", "commit"),
    ];
    expect(hex(drive(events).send)).toBe(hex("hi there "));
  });
});
