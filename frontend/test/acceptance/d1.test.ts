// D-1: Mic auto-Enter — the timer fired more than MAX_LATE_MS (1.5 s) late → no Enter, text kept, toast.
import { describe, expect, it } from "vitest";
import { MAX_LATE_MS, SUBMIT_MS } from "../../src/components/micSubmit";
import { micRig, runSubmit } from "./helpers/mic";
import micSrc from "../../src/components/Mic.tsx?raw";

describe("D-1 late timer", () => {
  it("waits SUBMIT_MS before Enter", async () => {
    const rig = micRig(["working"]);
    const r = await runSubmit(rig, 0);
    expect(rig.timers.some((x) => x.ms === SUBMIT_MS)).toBe(true);
    expect(r).toEqual({ entered: true });
    expect(rig.calls.filter((c) => c.startsWith("deliver"))).toEqual(["deliver w1:p1 hello there"]);
    expect(rig.calls[rig.calls.length - 1]).toBe("enter w1:p1");
  });
  it("on time and up to exactly MAX_LATE_MS late → Enter", async () => {
    for (const late of [0, 400, MAX_LATE_MS]) {
      const rig = micRig(["idle"]);
      expect(await runSubmit(rig, late), `late ${late}`).toEqual({ entered: true });
    }
  });
  it("more than MAX_LATE_MS late → no Enter, text kept, toast", async () => {
    const rig = micRig(["working"]);
    const r = await runSubmit(rig, MAX_LATE_MS + 100);
    expect(r).toEqual({ entered: false, reason: "late" });
    expect(rig.calls).not.toContain("enter w1:p1");
    expect(rig.calls.filter((c) => c.startsWith("deliver"))).toHaveLength(1);   // text delivered, kept
    expect(rig.toasts.length).toBeGreaterThan(0);
    expect(rig.toasts[rig.toasts.length - 1].message.trim()).not.toBe("");
  });
  it("very late (phone locked during the countdown) → no Enter", async () => {
    const rig = micRig(["idle"]);
    expect(await runSubmit(rig, 60_000)).toEqual({ entered: false, reason: "late" });
    expect(rig.calls).not.toContain("enter w1:p1");
  });
});

describe("D-1 wiring", () => {
  it("Mic.tsx runs the post-dictation sequence through submitDictation", () => {
    expect(micSrc).toMatch(/from\s+["']\.\/micSubmit["']/);
    expect(micSrc).toMatch(/submitDictation\s*\(/);
    expect(micSrc).not.toMatch(/sendKeys\s*\(/);       // Enter only via the guarded sequence
  });
});
