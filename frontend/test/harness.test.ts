import { describe, expect, it } from "vitest";
import { sanitizePaste } from "../src/input/sanitize";
import { createModifiers } from "../src/input/modifiers";

describe("vitest + jsdom harness", () => {
  it("has a DOM", () => {
    const el = document.createElement("textarea");
    el.value = "hi";
    document.body.appendChild(el);
    expect(document.querySelector("textarea")?.value).toBe("hi");
  });
  it("skeleton modules throw todo until implemented", () => {
    expect(() => sanitizePaste("x")).toThrow("todo");
    expect(() => createModifiers({ now: () => 0, onChange: () => {} })).toThrow("todo");
  });
});
