// C-3: sticky-modifier machine transitions (PRD 4.1), injected clock.
//   tap → once; tap again within DOUBLE_TAP_MS → locked; tap while once/locked after the window → off;
//   a tap after "once" was consumed → once. onChange fires on every change.
import { describe, expect, it } from "vitest";
import { createModifiers, DOUBLE_TAP_MS, type ModName } from "../../src/input/modifiers";
import type { ModState } from "../../src/types/term";
import hookSrc from "../../src/hooks/useHerdrTerminal.ts?raw";

function rig() {
  let t = 1000;
  const changes: [ModName, ModState][] = [];
  const m = createModifiers({ now: () => t, onChange: (n, s) => changes.push([n, s]) });
  return { m, changes, at: (ms: number) => { t = ms; } };
}

describe("C-3 modifier transitions", () => {
  it("starts off", () => {
    const { m } = rig();
    expect(m.state("ctrl")).toBe("off");
    expect(m.state("alt")).toBe("off");
  });
  it("tap → once", () => {
    const { m, changes } = rig();
    expect(m.tap("ctrl")).toBe("once");
    expect(m.state("ctrl")).toBe("once");
    expect(changes).toEqual([["ctrl", "once"]]);
  });
  it("second tap within DOUBLE_TAP_MS → locked", () => {
    const { m, at, changes } = rig();
    at(1000); m.tap("ctrl");
    at(1000 + DOUBLE_TAP_MS - 1);
    expect(m.tap("ctrl")).toBe("locked");
    expect(changes).toEqual([["ctrl", "once"], ["ctrl", "locked"]]);
  });
  it("second tap after the window → off", () => {
    const { m, at } = rig();
    at(1000); m.tap("ctrl");
    at(1000 + DOUBLE_TAP_MS + 1);
    expect(m.tap("ctrl")).toBe("off");
  });
  it("tap while locked (after the window) → off", () => {
    const { m, at } = rig();
    at(1000); m.tap("alt");
    at(1100); expect(m.tap("alt")).toBe("locked");
    at(1100 + DOUBLE_TAP_MS + 50);
    expect(m.tap("alt")).toBe("off");
  });
  it("once is consumed by one key; locked is not", () => {
    const { m, at, changes } = rig();
    at(1000); m.tap("ctrl");
    expect(m.apply("c", "key")).toBe("\x03");
    expect(m.state("ctrl")).toBe("off");
    expect(changes[changes.length - 1]).toEqual(["ctrl", "off"]);
    expect(m.apply("c", "key")).toBe("c");
    at(5000); m.tap("ctrl"); at(5100); m.tap("ctrl");
    expect(m.state("ctrl")).toBe("locked");
    expect(m.apply("c", "key")).toBe("\x03");
    expect(m.apply("d", "key")).toBe("\x04");
    expect(m.state("ctrl")).toBe("locked");
  });
  it("a tap after once was consumed → once, even inside the double-tap window", () => {
    const { m, at } = rig();
    at(1000); m.tap("ctrl");
    at(1100); m.apply("x", "key");
    at(1200);
    expect(m.tap("ctrl")).toBe("once");
  });
  it("reset → both off, with onChange", () => {
    const { m, at, changes } = rig();
    at(1000); m.tap("ctrl"); at(1100); m.tap("ctrl");
    at(3000); m.tap("alt");
    changes.length = 0;
    m.reset();
    expect(m.state("ctrl")).toBe("off");
    expect(m.state("alt")).toBe("off");
    expect(changes).toEqual(expect.arrayContaining([["ctrl", "off"], ["alt", "off"]]));
  });
  it("Ctrl table: a–z, space, @ [ \\ ] ^ _ ?", () => {
    const table: Record<string, string> = { " ": "\x00", "@": "\x00", "[": "\x1b", "\\": "\x1c", "]": "\x1d", "^": "\x1e", "_": "\x1f", "?": "\x7f" };
    for (let c = 0; c < 26; c++) table[String.fromCharCode(97 + c)] = String.fromCharCode(1 + c);
    for (const [ch, want] of Object.entries(table)) {
      const { m } = rig();
      m.tap("ctrl");
      expect(m.apply(ch, "key"), JSON.stringify(ch)).toBe(want);
      expect(m.state("ctrl")).toBe("off");
    }
  });
  it("Ctrl on an unmapped printable char: sent unmodified, modifier still consumed", () => {
    const { m } = rig();
    m.tap("ctrl");
    expect(m.apply("5", "key")).toBe("5");
    expect(m.state("ctrl")).toBe("off");
  });
  it("Alt once: ESC prefix on one printable char", () => {
    const { m } = rig();
    m.tap("alt");
    expect(m.apply("x", "key")).toBe("\x1bx");
    expect(m.state("alt")).toBe("off");
    expect(m.apply("x", "key")).toBe("x");
  });
});

describe("C-3 wiring", () => {
  it("the terminal hook uses the modifier machine", () => {
    expect(hookSrc).toMatch(/from\s+["']\.\.\/input\/modifiers["']/);
    expect(hookSrc).toMatch(/createModifiers\s*\(/);
  });
});
