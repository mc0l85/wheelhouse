// iPadOS "." shortcut on a hardware keyboard: replayed from a real iPad trace (2026-11-16).
// Letters go to the pane as xterm keys and never reach the textarea; spaces do. After "I am" the
// textarea held " I " and one space press read as a double space: the keyboard swapped the earlier
// space for "." and the pane showed "I am.typing".
import { describe, expect, it } from "vitest";
import { applyDel, drive, ev, ta } from "./helpers/composer";

const NB = " ";

describe("iOS period shortcut", () => {
  it("one space press after an untracked word sends only the space", () => {
    const run = drive([
      ev.data(0, " ", "other"),
      ev.before(10, "insertText", " ", ta("")),
      ev.input(14, "insertText", ta(NB)),
      ev.data(250, "I", "other"),
      ev.before(257, "insertText", "I", ta(NB)),
      ev.input(258, "insertText", ta(NB + "I")),
      ev.data(276, " ", "other"),
      ev.before(284, "insertText", " ", ta(NB + "I")),
      ev.input(285, "insertText", ta(NB + "I" + NB)),
      ev.data(444, "a", "other"),
      ev.data(573, "m", "other"),
      ev.data(659, " ", "other"),
      ev.before(667, "insertReplacementText", ".", ta(NB + "I" + NB, 2, 3)),
      ev.input(668, "insertReplacementText", ta(NB + "I.")),
      ev.before(679, "insertText", " ", ta("")),
      ev.input(680, "insertText", ta(NB)),
      ev.data(749, "t", "other"),
    ]);
    expect(applyDel(run.send)).toBe(" I am t");
    expect(run.effects.some((e) => e.type === "clearTextarea")).toBe(true);
  });

  it("collapsed-caret form of the shortcut is dropped too", () => {
    const run = drive([
      ev.data(0, "x", "other"),
      ev.data(10, " ", "other"),
      ev.before(20, "insertReplacementText", ". ", ta("x ")),
      ev.input(21, "insertReplacementText", ta("x. ")),
      ev.data(40, "y", "other"),
    ]);
    expect(applyDel(run.send)).toBe("x y");
  });

  it("a real autocorrect tail replacement is still sent", () => {
    const run = drive([
      ev.before(0, "insertReplacementText", "the", ta("teh", 0, 3)),
      ev.input(1, "insertReplacementText", ta("the")),
    ]);
    expect(run.send).toBe("\x7f\x7fhe");
  });
});
