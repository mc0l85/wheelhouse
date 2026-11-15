// C-11 (F9d): when the socket refused a send (send() === false) the composer resets, so the next
// correction emits no backspaces for text the pane never got; a socket reset (reopen) resets it too.
import { describe, expect, it } from "vitest";
import type { ComposerEvent } from "../../src/input/composer";
import { drive, ev, ta, sentAfter } from "./helpers/composer";

describe("C-11 composer reset", () => {
  it("sendFailed → state.comp is null and the next correction has no 7f", () => {
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
    const after = sentAfter(r, (e) => e.type === "sendFailed");
    expect(after).not.toContain("\x7f");
    expect(after.split("help").length - 1).toBe(1);
  });

  it("the state right after sendFailed / reset is not composing", () => {
    for (const evt of [ev.sendFailed(12), ev.reset(12)]) {
      const events: ComposerEvent[] = [ev.start(0, ta("")), ev.update(10, "hel", ta("")),
        ev.input(11, "insertCompositionText", ta("hel")), evt];
      const run = drive(events);
      expect(run.state.comp, evt.type).toBeNull();
    }
  });

  it("socket reset mid-composition → the continuing composition sends no 7f", () => {
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
    const after = sentAfter(r, (e) => e.type === "reset");
    expect(after).not.toContain("\x7f");
    expect(after).toContain("he");
  });
});
