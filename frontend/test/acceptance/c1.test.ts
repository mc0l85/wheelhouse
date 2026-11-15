// C-1: sanitizePaste drops ESC, C0 (keeps \t \n \r), DEL and C1 (U+0080–U+009F);
// the bracketed-paste breakout string comes out inert.
import { describe, expect, it } from "vitest";
import { OSC52_MAX_BYTES, sanitizePaste } from "../../src/input/sanitize";

const C0_DROPPED = Array.from({ length: 0x20 }, (_, i) => i).filter((c) => c !== 0x09 && c !== 0x0a && c !== 0x0d);

describe("C-1 sanitizePaste", () => {
  it("drops ESC", () => {
    expect(sanitizePaste("a\x1bb")).toBe("ab");
  });
  it("drops every C0 control except \\t \\n \\r", () => {
    for (const c of C0_DROPPED) expect(sanitizePaste(`x${String.fromCharCode(c)}y`), `0x${c.toString(16)}`).toBe("xy");
    expect(sanitizePaste("a\tb\nc\rd")).toBe("a\tb\nc\rd");
  });
  it("drops DEL", () => {
    expect(sanitizePaste("a\x7fb")).toBe("ab");
  });
  it("drops every C1 control U+0080–U+009F", () => {
    for (let c = 0x80; c <= 0x9f; c++) expect(sanitizePaste(`x${String.fromCharCode(c)}y`), `U+${c.toString(16)}`).toBe("xy");
  });
  it("keeps printable text, Latin-1 above C1, and astral characters", () => {
    const keep = "héllo wörld  ÿ ½ — “quotes” 日本語 👨‍👩‍👧 🇺🇸 é";
    expect(sanitizePaste(keep)).toBe(keep);
  });
  it("makes the bracketed-paste breakout inert", () => {
    const evil = "safe\x1b[201~rm -rf ~\r\x1b[200~";
    const out = sanitizePaste(evil);
    expect(out).toBe("safe[201~rm -rf ~\r[200~");
    expect(out.includes("\x1b")).toBe(false);
  });
  it("drops an 8-bit CSI (U+009B) breakout and an OSC title set", () => {
    expect(sanitizePaste("a\u009b201~b")).toBe("a201~b");
    expect(sanitizePaste("x\x1b]0;pwned\x07y")).toBe("x]0;pwnedy");
  });
  it("is the identity on clean text and handles the empty string", () => {
    expect(sanitizePaste("")).toBe("");
    expect(sanitizePaste("echo hi\n")).toBe("echo hi\n");
  });
  it("exports a positive OSC 52 byte cap", () => {
    expect(Number.isInteger(OSC52_MAX_BYTES) && OSC52_MAX_BYTES > 0).toBe(true);
  });
});
