// Lane F fixes (S3, S4, S5): exact bytes.
import { describe, expect, it } from "vitest";
import { drive, ev, hex, sentAfter, ta } from "./acceptance/helpers/composer";
import { isImeKeydown } from "../src/input/composer";

describe("S3: IME keydowns are not hardware keys", () => {
  it("keyCode 229 and isComposing are IME", () => {
    expect(isImeKeydown({ keyCode: 229 })).toBe(true);
    expect(isImeKeydown({ keyCode: 65, isComposing: true })).toBe(true);
  });
  it("a real key is not", () => {
    expect(isImeKeydown({ keyCode: 65, isComposing: false })).toBe(false);
    expect(isImeKeydown({ keyCode: 13 })).toBe(false);
  });
});

describe("S4: tray Enter mid-composition", () => {
  const live = [ev.start(0, ta("")), ev.update(5, "hello", ta("")), ev.input(6, "insertCompositionText", ta("hello"))];
  it("an unrelated commit after reset is sent, not dropped", () => {
    const r = drive([...live, ev.reset(10), ev.end(20, "hello", ta("hello")), ev.data(30, "7", "commit")]);
    expect(hex(sentAfter(r, (e) => e.type === "reset"))).toBe("37");
  });
  it("the abandoned composition's own commit is swallowed", () => {
    const r = drive([...live, ev.reset(10), ev.end(20, "hello", ta("hello")), ev.data(30, "hello", "commit")]);
    expect(sentAfter(r, (e) => e.type === "reset")).toBe("");
  });
  it("an escape sequence is never a commit", () => {
    const r = drive([...live, ev.reset(10), ev.end(20, "hello", ta("hello")), ev.data(30, "\x1b[?1;2c", "commit")]);
    expect(hex(sentAfter(r, (e) => e.type === "reset"))).toBe(hex("\x1b[?1;2c"));
  });
  it("reset arms an expiry timer when the composition had ended", () => {
    const r = drive([...live, ev.end(8, "hello", ta("hello")), ev.reset(10)]);
    expect(r.steps.find((x) => x.event.type === "reset")!.effects).toContainEqual({ type: "setTimer", id: "abandon", ms: 1500 });
    expect(r.state.abandoned).toBe(null);
  });
});

describe("S5: pending adopt settles without an input event", () => {
  it("textarea did not grow: adopt at the next update", () => {
    const r = drive([
      ev.start(0, ta("cat")),
      ev.update(1, "cat", ta("cat")),
      ev.update(2, "cats", ta("cat")),
    ]);
    expect(r.steps[1].send).toBe("");
    expect(hex(r.steps[2].send)).toBe("73");
  });
  it("textarea did not grow: adopt at the end", () => {
    const r = drive([ev.start(0, ta("cat")), ev.update(1, "cat", ta("cat")), ev.end(2, "cat", ta("cat"))]);
    expect(r.send).toBe("");
  });
});
