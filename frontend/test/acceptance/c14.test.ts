// C-14 (F9g/h): the late-commit duplicate guard drops ONLY xterm's own pending commit onData, once,
// and only when no composition happened in between; a second identical word with its own
// composition events inside the window is sent. (The tray Enter/^C textarea clear is the
// "tray-clear" e2e scenario, run by C-15.)
import { describe, expect, it } from "vitest";
import type { ComposerEvent } from "../../src/input/composer";
import { composeWord, drive, ev, hex, ta } from "./helpers/composer";

describe("C-14 duplicate commit window", () => {
  it("second identical composition inside the window is sent (commits never seen)", () => {
    // order: compose "ok", commit timer fires, compose "ok" again 300 ms later, timer fires; no xterm commit onData
    const a = composeWord(0, "", "ok");
    const b = composeWord(a.t + 300, "ok", "ok");
    expect(hex(drive([...a.events, ...b.events]).send)).toBe(hex("okok"));
  });

  it("second identical composition: both late commits dropped, each once", () => {
    // order: compose "ok", timer, late commit onData("ok") (dropped), compose "ok", timer,
    // late commit onData("ok") = "okok".substring(2) (dropped)
    const a = composeWord(0, "", "ok");
    const late1 = ev.data(a.t + 400, "ok", "commit");
    const b = composeWord(a.t + 600, "ok", "ok");
    const late2 = ev.data(b.t + 400, "ok", "commit");
    expect(hex(drive([...a.events, late1, ...b.events, late2]).send)).toBe(hex("okok"));
  });

  it("only the first matching onData is dropped: a repeat with no composition is sent", () => {
    // order: compose "ok", timer, late commit onData("ok") (dropped), onData("ok", other) 100 ms later (sent)
    const a = composeWord(0, "", "ok");
    const events: ComposerEvent[] = [...a.events, ev.data(a.t + 400, "ok", "commit"), ev.data(a.t + 500, "ok", "other")];
    expect(hex(drive(events).send)).toBe(hex("okok"));
  });

  it("a commit already matched before the timer does not arm the guard", () => {
    // order: compose "ok", commit onData("ok") before the timer (swallowed: already sent live),
    // then predictive inserts "ok" again (beforeinput/input insertText, onData("ok", other)) → sent
    const a = composeWord(0, "", "ok");
    const events: ComposerEvent[] = [
      ...a.events,
      ev.data(a.t + 1, "ok", "commit"),
      ev.before(a.t + 300, "insertText", "ok", ta("ok")),
      ev.input(a.t + 301, "insertText", ta("okok")),
      ev.data(a.t + 302, "ok", "other"),
    ];
    expect(hex(drive(events).send)).toBe(hex("okok"));
  });

  it("outside the window an identical onData is always sent", () => {
    const a = composeWord(0, "", "ok");
    const events: ComposerEvent[] = [...a.events, ev.data(a.t + 5000, "ok", "other")];
    expect(hex(drive(events).send)).toBe(hex("okok"));
  });
});
