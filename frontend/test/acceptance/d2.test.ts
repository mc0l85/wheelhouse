// D-2: Mic auto-Enter skipped (text kept, toast) when the pane is blocked now, gone, or the status
// fetch failed; a target already blocked at deliver time → the text is not sent at all.
import { describe, expect, it } from "vitest";
import { micRig, runSubmit } from "./helpers/mic";

describe("D-2 skip Enter", () => {
  it("target blocked at deliver time → text not sent, no Enter, toast", async () => {
    const rig = micRig(["blocked"]);
    const r = await runSubmit(rig, 0);
    expect(r).toEqual({ entered: false, reason: "not-delivered" });
    expect(rig.calls.some((c) => c.startsWith("deliver"))).toBe(false);
    expect(rig.calls.some((c) => c.startsWith("enter"))).toBe(false);
    expect(rig.toasts.length).toBeGreaterThan(0);
  });

  const cases: [string, Parameters<typeof micRig>[0], string][] = [
    ["pane blocked now", ["working", "blocked"], "blocked"],
    ["pane gone", ["working", null], "gone"],
    ["status fetch failed", ["working", "reject"], "status-failed"],
  ];
  for (const [name, statuses, reason] of cases) {
    it(`${name} → no Enter, text kept, toast`, async () => {
      const rig = micRig(statuses);
      const r = await runSubmit(rig, 0);
      expect(r).toEqual({ entered: false, reason });
      expect(rig.calls.filter((c) => c.startsWith("deliver"))).toEqual(["deliver w1:p1 hello there"]);
      expect(rig.calls.some((c) => c.startsWith("enter"))).toBe(false);
      expect(rig.toasts.length).toBeGreaterThan(0);
      // the re-check happens at Enter time, after the delivery
      const d = rig.calls.findIndex((c) => c.startsWith("deliver"));
      expect(rig.calls.slice(d + 1).some((c) => c.startsWith("status"))).toBe(true);
    });
  }

  it("not blocked at either check → Enter goes to the same pane", async () => {
    const rig = micRig(["idle", "working"]);
    expect(await runSubmit(rig, 0, "w2:p7", "ship it")).toEqual({ entered: true });
    expect(rig.calls.filter((c) => !c.startsWith("status"))).toEqual(["deliver w2:p7 ship it", "enter w2:p7"]);
  });
});
