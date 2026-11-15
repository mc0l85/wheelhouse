// Autocorrect taps with a collapsed caret at the end of the textarea: the replacement is a tail
// replacement even when the last letter survives ("dont" -> "don't"). Exact bytes + resulting line.
import { describe, expect, it } from "vitest";
import { applyDel, drive, ev, hex, ta } from "./acceptance/helpers/composer";

function tap(before: string, after: string, caret = before.length) {
  return drive([
    ev.before(0, "insertReplacementText", after, ta(before, caret, caret)),
    ev.input(1, "insertReplacementText", ta(after, after.length, after.length)),
  ]);
}

describe("collapsed-caret autocorrect at the end", () => {
  const cases: [string, string, string][] = [
    ["dont", "don't", "7f 27 74"],
    ["im", "I'm", "7f 7f 49 27 6d"],
    ["recieve", "receive", "7f 7f 7f 7f 65 69 76 65"],
    ["teh ", "the ", "7f 7f 7f 68 65 20"],
  ];
  for (const [before, after, bytes] of cases) {
    it(`${JSON.stringify(before)} -> ${JSON.stringify(after)}`, () => {
      const r = tap(before, after);
      expect(hex(r.send)).toBe(bytes);
      expect(applyDel(before + r.send)).toBe(after);
      expect(r.effects.some((e) => e.type === "toast" || e.type === "clearTextarea")).toBe(false);
    });
  }
  it("caret NOT at the end: nothing sent, textarea cleared, toast", () => {
    const r = tap("teh cat", "the cat", 3);
    expect(r.send).toBe("");
    expect(r.effects).toContainEqual({ type: "clearTextarea" });
    expect(r.effects.some((e) => e.type === "toast")).toBe(true);
  });
});

describe("sendFailed outside a composition clears the stale textarea tail", () => {
  it("emits clearTextarea", () => {
    const r = drive([ev.sendFailed(0)]);
    expect(r.effects).toContainEqual({ type: "clearTextarea" });
  });
  it("but not in the middle of a composition", () => {
    const r = drive([ev.start(0, ta("")), ev.update(5, "a", ta("")), ev.sendFailed(6)]);
    expect(r.steps[2].effects.some((e) => e.type === "clearTextarea")).toBe(false);
  });
});
