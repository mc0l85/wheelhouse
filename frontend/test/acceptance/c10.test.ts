// C-10 (F9c): a keyboard re-composing a word the pane already has (Gboard / iOS tapping into a word)
// must not retype the word; a genuinely new composition right after text still types.
import { describe, expect, it } from "vitest";
import { drive, ev, hex, ta } from "./helpers/composer";

describe("C-10 re-composition", () => {
  it('re-compose "hello" (no selection) and correct it to "help" → 7f 7f "p"', () => {
    // order: compositionstart (cursor after "hello"), update "hello" (adopts the existing word: the
    // textarea does not change), input, update "help", input (textarea "help"), compositionend "help".
    // xterm's commit = "help".substring(5) = "" → no commit onData.
    const r = drive([
      ev.start(0, ta("hello")),
      ev.update(10, "hello", ta("hello")),
      ev.input(11, "insertCompositionText", ta("hello")),
      ev.update(500, "help", ta("hello")),
      ev.input(501, "insertCompositionText", ta("help")),
      ev.end(600, "help", ta("help")),
    ]);
    expect(hex(r.send)).toBe(hex("\x7f\x7fp"));
  });

  it("re-compose a word and leave it unchanged → zero bytes", () => {
    const r = drive([
      ev.start(0, ta("hello")),
      ev.update(10, "hello", ta("hello")),
      ev.input(11, "insertCompositionText", ta("hello")),
      ev.end(300, "hello", ta("hello")),
    ]);
    expect(hex(r.send)).toBe("");
  });

  it('a new composition "a" right after "banana" is typed (not mistaken for a re-composition)', () => {
    // order: hardware "banana", compositionstart, update "a", input (textarea grew to "bananaa"),
    // compositionend "a", xterm commit onData("a") = "bananaa".substring(6)
    const hw = Array.from("banana", (ch, i) => ev.data(i * 30, ch, "other"));
    const r = drive([
      ...hw,
      ev.start(500, ta("banana")),
      ev.update(510, "a", ta("banana")),
      ev.input(511, "insertCompositionText", ta("bananaa")),
      ev.end(560, "a", ta("bananaa")),
      ev.data(561, "a", "commit"),
    ]);
    expect(hex(r.send)).toBe(hex("bananaa"));
  });
});
