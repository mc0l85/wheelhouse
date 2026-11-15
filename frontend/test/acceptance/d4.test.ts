// D-4: a handshake not open within CONNECT_TIMEOUT_MS is abandoned and retried.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CONNECT_TIMEOUT_MS, onClose } from "../../src/hooks/socketPolicy";
import { FakeWS, last, sockRig } from "./helpers/fakeSocket";

describe("D-4 connect timeout", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("a never-opening handshake is closed and a new socket made", () => {
    const rig = sockRig();
    rig.sock.connect();
    const first = last();
    vi.advanceTimersByTime(CONNECT_TIMEOUT_MS - 50);
    expect(FakeWS.all).toHaveLength(1);
    expect(first.closedByClient).toBe(false);
    const firstBackoff = onClose(1006, "", false, 1).delayMs;   // the policy's first never-opened retry delay
    vi.advanceTimersByTime(50 + firstBackoff + 10);
    expect(first.closedByClient).toBe(true);
    expect(FakeWS.all.length).toBeGreaterThanOrEqual(2);
    first.open();                                       // a late open of the abandoned socket is ignored
    expect(rig.opens).toBe(0);
    last().open();
    expect(rig.opens).toBe(1);
    rig.sock.dispose();
  });

  it("a socket that opens in time is kept", () => {
    const rig = sockRig();
    rig.sock.connect();
    vi.advanceTimersByTime(100);
    last().open();
    vi.advanceTimersByTime(CONNECT_TIMEOUT_MS + 1000);   // < the 15 s ping interval
    expect(FakeWS.all).toHaveLength(1);
    expect(FakeWS.all[0].closedByClient).toBe(false);
    rig.sock.dispose();
  });
});
