// C-13 (F9f): backspace unit = grapheme cluster (Decision 9: Intl.Segmenter; the Claude prompt
// deletes one grapheme per DEL). Exact bytes stated per case (UTF-8 hex).
//   👨‍👩‍👧 = U+1F468 U+200D U+1F469 U+200D U+1F467  (5 code points, 1 grapheme)
//   🇺🇸 = U+1F1FA U+1F1F8                          (2 code points, 1 grapheme)
//   é  = e U+0301                                   (2 code points, 1 grapheme)
import { describe, expect, it } from "vitest";
import { drive, ev, hex, ta } from "./helpers/composer";

const FAMILY = "\u{1F468}‍\u{1F469}‍\u{1F467}";
const PARENTS = "\u{1F468}‍\u{1F469}";
const FLAG = "\u{1F1FA}\u{1F1F8}";
const E_ACUTE = "é";

function recompose(from: string, to: string) {
  // order: start, update `from`, input, update `to` (textarea before = from), input → bytes of the 2nd update
  const r = drive([
    ev.start(0, ta("")),
    ev.update(10, from, ta("")),
    ev.input(11, "insertCompositionText", ta(from)),
    ev.update(100, to, ta(from)),
    ev.input(101, "insertCompositionText", ta(to)),
  ]);
  return r.steps.slice(3).map((s) => s.send).join("");
}

describe("C-13 grapheme backspace", () => {
  it('ZWJ family removed: "ok👨‍👩‍👧" → "ok" = 7f', () => {
    expect(hex(recompose("ok" + FAMILY, "ok"))).toBe("7f");
  });
  it('flag removed: "us🇺🇸" → "us" = 7f', () => {
    expect(hex(recompose("us" + FLAG, "us"))).toBe("7f");
  });
  it('combining accent: "café" (e+U+0301) → "cafe" = 7f 65', () => {
    expect(hex(recompose("caf" + E_ACUTE, "cafe"))).toBe("7f 65");
  });
  it('ZWJ family shrinks to parents: 👨‍👩‍👧 → 👨‍👩 = 7f f0 9f 91 a8 e2 80 8d f0 9f 91 a9', () => {
    expect(hex(recompose(FAMILY, PARENTS))).toBe("7f f0 9f 91 a8 e2 80 8d f0 9f 91 a9");
  });
  it("selected tail of two graphemes replaced by insertText: 7f 7f 62", () => {
    // order: beforeinput insertText "b" with "👨‍👩‍👧🇺🇸" selected at the end of "a👨‍👩‍👧🇺🇸", input, xterm onData("b")
    const v = "a" + FAMILY + FLAG;
    const r = drive([
      ev.before(0, "insertText", "b", ta(v, 1, v.length)),
      ev.input(1, "insertText", ta("ab")),
      ev.data(2, "b", "other"),
    ]);
    expect(hex(r.send)).toBe("7f 7f 62");
  });
  it("dictation replacing a selected tail ending in e+U+0301: one 7f for it", () => {
    // order: compositionstart with "café" selected, update "cafe", input, end, commit = "cafe".substring(4) = ""
    const v = "caf" + E_ACUTE;
    const r = drive([
      ev.start(0, ta(v, 0, v.length)),
      ev.update(5, "cafe", ta(v, 0, v.length)),
      ev.input(6, "insertCompositionText", ta("cafe")),
      ev.end(10, "cafe", ta("cafe")),
    ]);
    expect(hex(r.send)).toBe("7f 65");
  });
});
