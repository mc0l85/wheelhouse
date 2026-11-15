// C-11 (F9d): the composer after a failed send and after a socket reset.
//   sendFailed — the bytes were NOT delivered: forget them. The next correction sends the word again,
//                with no backspaces for text the pane never got.
//   reset      — the socket reopened (onReset); bytes sent before the drop probably DID reach the pane.
//                The composition in progress is ABANDONED: nothing more is sent for it (no update,
//                no end, not xterm's commit onData for it) until the next compositionstart. This
//                never doubles text ("hel" + "hello") and never erases text it may not have delivered.
import { describe, expect, it } from "vitest";
import type { ComposerEvent } from "../../src/input/composer";
import { composeWord, drive, ev, hex, ta, sentAfter } from "./helpers/composer";

describe("C-11 sendFailed", () => {
  it("sendFailed → nothing sent for it; the next correction resends the word once, no 7f", () => {
    // order: start, update "hel" (its bytes were NOT delivered), sendFailed, update "help", input, end, commit
    const r = drive([
      ev.start(0, ta("")),
      ev.update(10, "hel", ta("")),
      ev.input(11, "insertCompositionText", ta("hel")),
      ev.sendFailed(12),
      ev.update(80, "help", ta("hel")),
      ev.input(81, "insertCompositionText", ta("help")),
      ev.end(140, "help", ta("help")),
      ev.data(141, "help", "commit"),
    ]);
    const fail = r.steps.findIndex((s) => s.event.type === "sendFailed");
    expect(r.steps[fail].send).toBe("");
    expect(hex(sentAfter(r, (e) => e.type === "sendFailed"))).toBe(hex("help"));
  });

  it("the state right after sendFailed / reset has comp === null", () => {
    for (const evt of [ev.sendFailed(12), ev.reset(12)]) {
      const events: ComposerEvent[] = [ev.start(0, ta("")), ev.update(10, "hel", ta("")),
        ev.input(11, "insertCompositionText", ta("hel")), evt];
      expect(drive(events).state.comp, evt.type).toBeNull();
    }
  });
});

describe("C-11 reset abandons the composition in progress", () => {
  it('"hel" delivered, reset, update "hello" … end, commit → zero bytes after the reset (no "helhello")', () => {
    // order: start, update "hel", input, reset (socket reopened), update "hello", input, end "hello",
    // xterm commit onData("hello")
    const r = drive([
      ev.start(0, ta("")),
      ev.update(10, "hel", ta("")),
      ev.input(11, "insertCompositionText", ta("hel")),
      ev.reset(200),
      ev.update(300, "hello", ta("hel")),
      ev.input(301, "insertCompositionText", ta("hello")),
      ev.end(360, "hello", ta("hello")),
      ev.data(361, "hello", "commit"),
    ]);
    expect(hex(r.send)).toBe(hex("hel"));
    expect(hex(sentAfter(r, (e) => e.type === "reset"))).toBe("");
  });

  it('a shrinking correction after the reset ("hel" → "he") sends no 7f and nothing else', () => {
    const r = drive([
      ev.start(0, ta("")),
      ev.update(10, "hel", ta("")),
      ev.input(11, "insertCompositionText", ta("hel")),
      ev.reset(200),
      ev.update(300, "he", ta("hel")),
      ev.input(301, "insertCompositionText", ta("he")),
      ev.end(360, "he", ta("he")),
      ev.data(361, "he", "commit"),
    ]);
    expect(hex(sentAfter(r, (e) => e.type === "reset"))).toBe("");
  });

  it("the next composition after an abandoned one is typed normally", () => {
    // order: "hel" composed, reset, its end + commit (dropped), a space (textarea change), then a new
    // word "ok" composed from base "hel "
    const next = composeWord(1000, "hel ", "ok");
    const r = drive([
      ev.start(0, ta("")),
      ev.update(10, "hel", ta("")),
      ev.input(11, "insertCompositionText", ta("hel")),
      ev.reset(200),
      ev.end(300, "hel", ta("hel")),
      ev.data(301, "hel", "commit"),
      ev.input(500, "insertText", ta("hel ")),
      ev.data(501, " ", "textareaChange"),
      ...next.events,
      ev.data(next.t + 1, "ok", "commit"),
    ]);
    expect(hex(sentAfter(r, (e) => e.type === "reset"))).toBe(hex(" ok"));
  });

  it("a reset with no composition in progress changes nothing for the next word", () => {
    const w = composeWord(100, "", "hi");
    const r = drive([ev.reset(0), ...w.events, ev.data(w.t + 1, "hi", "commit")]);
    expect(hex(r.send)).toBe(hex("hi"));
  });
});
