// C-4: paste and report sources are never modified and never consume a modifier, including a
// one-char paste (bracketed mode off); Alt works on \r, \t and \x7f.
import { describe, expect, it } from "vitest";
import { createModifiers } from "../../src/input/modifiers";

const mk = () => createModifiers({ now: () => 0, onChange: () => {} });

describe("C-4 paste/report pass-through", () => {
  for (const source of ["paste", "report"] as const) {
    it(`${source}: Ctrl once is neither applied nor consumed`, () => {
      const m = mk();
      m.tap("ctrl");
      expect(m.apply("c", source)).toBe("c");                 // one-char paste, bracketed mode off
      expect(m.apply("hello\r", source)).toBe("hello\r");
      expect(m.apply("\x1b[I", source)).toBe("\x1b[I");        // focus report
      expect(m.apply("\x1b[<0;10;5M", source)).toBe("\x1b[<0;10;5M");  // SGR mouse report
      expect(m.state("ctrl")).toBe("once");
      expect(m.apply("c", "key")).toBe("\x03");               // still armed for the next real key
    });
    it(`${source}: Alt once is neither applied nor consumed`, () => {
      const m = mk();
      m.tap("alt");
      expect(m.apply("x", source)).toBe("x");
      expect(m.state("alt")).toBe("once");
    });
  }
});

describe("C-4 Alt on control keys", () => {
  for (const [ch, name] of [["\r", "CR"], ["\t", "Tab"], ["\x7f", "DEL"]] as const) {
    it(`Alt + ${name}`, () => {
      const m = mk();
      m.tap("alt");
      expect(m.apply(ch, "key")).toBe("\x1b" + ch);
      expect(m.state("alt")).toBe("off");
    });
    it(`Alt + ${name} from the tray`, () => {
      const m = mk();
      m.tap("alt");
      expect(m.apply(ch, "tray")).toBe("\x1b" + ch);
      expect(m.state("alt")).toBe("off");
    });
  }
});
