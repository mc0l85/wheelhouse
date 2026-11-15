// C-5: a composing soft keyboard (iOS / Gboard type every letter as a composition).
//   Ctrl once + compositionupdate "c" → exactly 0x03 and a consumeModifier effect;
//   the composition carrying on ("ca") or ending ("c") sends no "c" and no 0x7f for it;
//   the modifier machine clears "once" (indicator) when consumed from a composition or the tray.
import { describe, expect, it } from "vitest";
import { createModifiers, type ModName } from "../../src/input/modifiers";
import type { ModState } from "../../src/types/term";
import { drive, ev, hex, OFF, ta } from "./helpers/composer";

const CTRL_ONCE = { ctrl: "once", alt: "off" } as const;
const CTRL_LOCKED = { ctrl: "locked", alt: "off" } as const;
const ALT_ONCE = { ctrl: "off", alt: "once" } as const;

describe("C-5 composer: modifier on a composition's first letter", () => {
  it('Ctrl once, compose "c", end "c" → exactly 03', () => {
    // order: start, update "c" (mods ctrl once), input, end "c", xterm commit onData "c"
    const r = drive([
      ev.start(0, ta(""), CTRL_ONCE),
      ev.update(10, "c", ta(""), CTRL_ONCE),
      ev.input(11, "insertCompositionText", ta("c")),
      ev.end(60, "c", ta("c")),
      ev.data(61, "c", "commit"),
    ]);
    expect(hex(r.send)).toBe(hex("\x03"));
    expect(r.effects.filter((e) => e.type === "consumeModifier")).toEqual([{ type: "consumeModifier", m: "ctrl" }]);
  });

  it('Ctrl once, compose "c" then "ca" → 03 then "a": no "c", no 7f', () => {
    // order: start, update "c" (ctrl once), input, update "ca" (mods now off), input, end "ca", commit "ca"
    const r = drive([
      ev.start(0, ta(""), CTRL_ONCE),
      ev.update(10, "c", ta(""), CTRL_ONCE),
      ev.input(11, "insertCompositionText", ta("c")),
      ev.update(80, "ca", ta("c"), OFF),
      ev.input(81, "insertCompositionText", ta("ca")),
      ev.end(140, "ca", ta("ca")),
      ev.data(141, "ca", "commit"),
    ]);
    expect(hex(r.send)).toBe(hex("\x03a"));
    expect(r.send).not.toContain("c");
    expect(r.send).not.toContain("\x7f");
  });

  it("Ctrl locked: applied, not consumed", () => {
    const r = drive([
      ev.start(0, ta(""), CTRL_LOCKED),
      ev.update(10, "c", ta(""), CTRL_LOCKED),
      ev.input(11, "insertCompositionText", ta("c")),
      ev.end(60, "c", ta("c")),
      ev.data(61, "c", "commit"),
    ]);
    expect(hex(r.send)).toBe(hex("\x03"));
    expect(r.effects.some((e) => e.type === "consumeModifier")).toBe(false);
  });

  it("Alt once on a composed letter → ESC x", () => {
    const r = drive([
      ev.start(0, ta(""), ALT_ONCE),
      ev.update(10, "x", ta(""), ALT_ONCE),
      ev.input(11, "insertCompositionText", ta("x")),
      ev.end(60, "x", ta("x")),
      ev.data(61, "x", "commit"),
    ]);
    expect(hex(r.send)).toBe(hex("\x1bx"));
    expect(r.effects.filter((e) => e.type === "consumeModifier")).toEqual([{ type: "consumeModifier", m: "alt" }]);
  });

  it("no modifier: the same composition is plain text", () => {
    const r = drive([
      ev.start(0, ta("")),
      ev.update(10, "c", ta("")),
      ev.input(11, "insertCompositionText", ta("c")),
      ev.end(60, "c", ta("c")),
      ev.data(61, "c", "commit"),
    ]);
    expect(hex(r.send)).toBe(hex("c"));
    expect(r.effects.some((e) => e.type === "consumeModifier")).toBe(false);
  });
});

describe("C-5 modifier machine: indicator clears when once is consumed", () => {
  const rig = () => {
    const changes: [ModName, ModState][] = [];
    const m = createModifiers({ now: () => 0, onChange: (n, s) => changes.push([n, s]) });
    return { m, changes };
  };
  it("composition source consumes once and reports off", () => {
    const { m, changes } = rig();
    m.tap("ctrl");
    expect(m.apply("c", "composition")).toBe("\x03");
    expect(m.state("ctrl")).toBe("off");
    expect(changes[changes.length - 1]).toEqual(["ctrl", "off"]);
  });
  it("tray keys consume once (mapped and unmapped)", () => {
    const { m, changes } = rig();
    m.tap("ctrl");
    expect(m.apply("\\", "tray")).toBe("\x1c");
    expect(m.state("ctrl")).toBe("off");
    expect(changes[changes.length - 1]).toEqual(["ctrl", "off"]);
    m.tap("ctrl");
    expect(m.apply("/", "tray")).toBe("/");                    // unmapped: unmodified, still consumed
    expect(m.state("ctrl")).toBe("off");
  });
});
