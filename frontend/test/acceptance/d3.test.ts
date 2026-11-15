// D-3: 1008 / 1013 / 4503 / 4500 and "N closes without ever opening" → a visible message, retry
// with backoff. socketPolicy.onClose is pure; terminalSocket must use it.
//
// Pinned (signature extended from the H0 skeleton, see the ledger notes):
//   onClose(code, reason, everOpened, failStreak) — failStreak = consecutive closes of sockets
//   that never opened, counting this one (0 when this socket had opened).
//   NEVER_OPENED_LIMIT — from this many never-opened closes in a row, a message is shown.
//   A refusal's message contains the server's close reason when one was given.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NEVER_OPENED_LIMIT, onClose } from "../../src/hooks/socketPolicy";
import { FakeWS, last, sockRig } from "./helpers/fakeSocket";

const REFUSALS: [number, string][] = [
  [1008, "origin not allowed"], [1013, "terminal limit reached"], [1013, "output backlog"],
  [4503, "herdr server not running"], [4500, "herdr binary missing"],
];

describe("D-3 socketPolicy.onClose", () => {
  for (const [code, reason] of REFUSALS) {
    it(`${code} "${reason}" → message with the reason, retry with a delay`, () => {
      for (const everOpened of [true, false]) {
        const d = onClose(code, reason, everOpened, everOpened ? 0 : 1);
        expect(d.retry).toBe(true);
        expect(d.delayMs).toBeGreaterThan(0);
        expect(d.message ?? "").toContain(reason);
      }
    });
    it(`${code} with no reason still shows a message`, () => {
      expect((onClose(code, "", true, 0).message ?? "").trim()).not.toBe("");
    });
  }

  it("an ordinary drop after being open → silent retry", () => {
    for (const code of [1006, 1001, 1012]) {
      const d = onClose(code, "", true, 0);
      expect(d.retry).toBe(true);
      expect(d.message).toBeUndefined();
    }
  });

  it("N closes without ever opening → message from NEVER_OPENED_LIMIT on", () => {
    expect(Number.isInteger(NEVER_OPENED_LIMIT) && NEVER_OPENED_LIMIT >= 2).toBe(true);
    for (let n = 1; n < NEVER_OPENED_LIMIT; n++) expect(onClose(1006, "", false, n).message, `streak ${n}`).toBeUndefined();
    for (let n = NEVER_OPENED_LIMIT; n < NEVER_OPENED_LIMIT + 3; n++)
      expect((onClose(1006, "", false, n).message ?? "").trim(), `streak ${n}`).not.toBe("");
  });

  it("backoff grows with the never-opened streak and is capped", () => {
    const ds = Array.from({ length: 12 }, (_, i) => onClose(1006, "", false, i + 1).delayMs);
    for (let i = 1; i < ds.length; i++) expect(ds[i]).toBeGreaterThanOrEqual(ds[i - 1]);
    expect(ds[ds.length - 1]).toBeGreaterThan(ds[0]);
    expect(ds[ds.length - 1]).toBeLessThanOrEqual(60_000);
    for (const d of ds) expect(d).toBeGreaterThan(0);
  });
});

describe("D-3 terminalSocket uses the policy", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  for (const [code, reason] of [[4503, "e2e herdr server not running"], [1013, "e2e terminal limit"]] as const) {
    it(`close ${code} after open → onConn carries the message, then a new socket`, () => {
      const rig = sockRig();
      rig.sock.connect();
      last().open();
      last().serverClose(code, reason);
      const msg = rig.conn.filter((c) => c.message).map((c) => c.message!);
      expect(msg.some((m) => m.includes(reason)), JSON.stringify(rig.conn)).toBe(true);
      vi.advanceTimersByTime(60_000);
      expect(FakeWS.all.length).toBeGreaterThanOrEqual(2);
      rig.sock.dispose();
    });
  }

  it("refused before open (handshake closes) NEVER_OPENED_LIMIT times → message", () => {
    const rig = sockRig();
    rig.sock.connect();
    for (let i = 0; i < NEVER_OPENED_LIMIT; i++) {
      const s = last();
      s.serverClose(1006, "");
      for (let waited = 0; FakeWS.all.length < i + 2 && waited <= 60_000; waited += 100) vi.advanceTimersByTime(100);
      expect(FakeWS.all.length, `retry ${i + 1}`).toBe(i + 2);
    }
    expect(rig.conn.some((c) => (c.message ?? "").trim() !== "")).toBe(true);
    rig.sock.dispose();
  });

  it("an ordinary drop after open shows no message", () => {
    const rig = sockRig();
    rig.sock.connect();
    last().open();
    last().serverClose(1006, "");
    expect(rig.conn.some((c) => c.message)).toBe(false);
    rig.sock.dispose();
  });
});
