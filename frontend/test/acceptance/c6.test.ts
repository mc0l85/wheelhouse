// C-6 regression: iPhone dictation replaces its previous hypothesis each time → the pane ends with
// clean final text. Synthetic traces matching the e2e dictation scenarios (no recorded traces exist yet).
import { describe, expect, it } from "vitest";
import type { ComposerEvent } from "../../src/input/composer";
import { applyDel, drive, ev, ta } from "./helpers/composer";
import hookSrc from "../../src/hooks/useHerdrTerminal.ts?raw";

const PHRASES = ["t", "take", "take a", "take a look", "take a look at", "take a look at it."];
const FINAL = "take a look at it.";

describe("C-6 dictation replacement", () => {
  it("insertText mode: each hypothesis selects the previous one and inserts the whole phrase", () => {
    // per hypothesis h: beforeinput insertText(h) over the selected previous text,
    // input insertText (textarea now h), xterm onData(h, other)
    const events: ComposerEvent[] = [];
    let prev = "", t = 0;
    for (const h of PHRASES) {
      events.push(ev.before(t, "insertText", h, ta(prev, 0, prev.length)));
      events.push(ev.input(t + 1, "insertText", ta(h)));
      events.push(ev.data(t + 2, h, "other"));
      prev = h; t += 400;
    }
    const r = drive(events);
    expect(applyDel(r.send)).toBe(FINAL);
  });

  it("composition mode: each hypothesis is a new composition over the selected previous text", () => {
    // per hypothesis h: compositionstart (previous text selected), compositionupdate h (textarea before),
    // input insertCompositionText (textarea now h), compositionend h,
    // xterm commit onData = h.substring(previous length) (only when non-empty)
    const events: ComposerEvent[] = [];
    let prev = "", t = 0;
    for (const h of PHRASES) {
      const sel = ta(prev, 0, prev.length);
      events.push(ev.start(t, sel));
      events.push(ev.update(t + 5, h, sel));
      events.push(ev.input(t + 6, "insertCompositionText", ta(h)));
      events.push(ev.end(t + 10, h, ta(h)));
      const commit = h.substring(prev.length);
      if (commit) events.push(ev.data(t + 11, commit, "commit"));
      prev = h; t += 400;
    }
    const r = drive(events);
    expect(applyDel(r.send)).toBe(FINAL);
  });

  it("the terminal hook routes input through the composer", () => {
    expect(hookSrc).toMatch(/from\s+["']\.\.\/input\/composer["']/);
    expect(hookSrc).toMatch(/\breduce\s*\(/);
  });
});
