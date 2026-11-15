// C-12 (F9e): a replacement that is NOT at the end of the textarea (autocorrect / a tapped
// suggestion on an earlier word) cannot be mapped onto the pane's cursor: zero bytes, for both
// insertText and insertReplacementText, then a clearTextarea effect.
import { describe, expect, it } from "vitest";
import { applyDel, drive, ev, ta } from "./helpers/composer";

describe("C-12 non-tail replacement", () => {
  it("insertText over a non-tail selection → 0 bytes + clearTextarea", () => {
    // order: beforeinput insertText "x" with "teh" (0..3) selected in "teh cat", input, xterm onData("x", other)
    const r = drive([
      ev.before(0, "insertText", "x", ta("teh cat", 0, 3)),
      ev.input(1, "insertText", ta("x cat", 1, 1)),
      ev.data(2, "x", "other"),
    ]);
    expect(r.send).toBe("");
    expect(r.effects).toContainEqual({ type: "clearTextarea" });
  });

  for (const data of ["the", null]) {
    it(`insertReplacementText (data ${JSON.stringify(data)}) on a non-tail word → 0 bytes + clearTextarea`, () => {
      // order: beforeinput insertReplacementText with "teh" selected, input (textarea "the cat"); xterm sends nothing
      const r = drive([
        ev.before(0, "insertReplacementText", data, ta("teh cat", 0, 3)),
        ev.input(1, "insertReplacementText", ta("the cat", 3, 3)),
      ]);
      expect(r.send).toBe("");
      expect(r.effects).toContainEqual({ type: "clearTextarea" });
    });
  }

  it("a tail replacement sends the grapheme-prefix-minimal edit (control)", () => {
    // order: beforeinput insertReplacementText with "teh" (4..7) selected at the end, input.
    // "teh" → "the" keeps "t": 7f 7f "he" (PRD 4.1 prefix-minimal rule, as in C-10 / C-13)
    const r = drive([
      ev.before(0, "insertReplacementText", "the", ta("cat teh", 4, 7)),
      ev.input(1, "insertReplacementText", ta("cat the", 7, 7)),
    ]);
    expect(r.send).toBe("\x7f\x7fhe");
    expect(applyDel("cat teh" + r.send)).toBe("cat the");
  });
});
