// C-9 (F9b): a key pressed mid-composition makes xterm finalize the composition on keydown:
// start, update "hel", onData("hel", keydownFinalize), onData("\r"), end → exactly "hel\r".
import { describe, expect, it } from "vitest";
import { drive, ev, hex, ta } from "./helpers/composer";

describe("C-9 keydown finalize", () => {
  it("Enter during a composition", () => {
    // order: compositionstart, compositionupdate "hel", input, xterm onData("hel", keydownFinalize),
    // xterm onData("\r", other) for the Enter key, compositionend "hel"
    const r = drive([
      ev.start(0, ta("")),
      ev.update(10, "hel", ta("")),
      ev.input(11, "insertCompositionText", ta("hel")),
      ev.data(50, "hel", "keydownFinalize"),
      ev.data(51, "\r", "other"),
      ev.end(60, "hel", ta("hel")),
    ]);
    expect(hex(r.send)).toBe(hex("hel\r"));
  });

  it("letter by letter, then Enter", () => {
    const r = drive([
      ev.start(0, ta("")),
      ev.update(10, "h", ta("")),
      ev.input(11, "insertCompositionText", ta("h")),
      ev.update(70, "he", ta("h")),
      ev.input(71, "insertCompositionText", ta("he")),
      ev.update(130, "hel", ta("he")),
      ev.input(131, "insertCompositionText", ta("hel")),
      ev.data(200, "hel", "keydownFinalize"),
      ev.data(201, "\r", "other"),
      ev.end(210, "hel", ta("hel")),
    ]);
    expect(hex(r.send)).toBe(hex("hel\r"));
  });
});
