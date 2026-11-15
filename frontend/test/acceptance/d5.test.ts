// D-5: a reconnect requested while the socket is still CONNECTING replaces the pending socket.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeWS, last, sockRig } from "./helpers/fakeSocket";

describe("D-5 reconnect while connecting", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("reconnectNow() during CONNECTING closes the pending socket and opens a new one", () => {
    const rig = sockRig();
    rig.sock.connect();
    const pending = last();
    expect(pending.readyState).toBe(FakeWS.CONNECTING);
    rig.sock.reconnectNow();
    vi.advanceTimersByTime(1000);
    expect(pending.closedByClient).toBe(true);
    expect(FakeWS.all).toHaveLength(2);
    expect(rig.sock.current()).toBe(FakeWS.all[1] as unknown as WebSocket);
    pending.open();                                    // the replaced socket's late open is ignored
    expect(rig.opens).toBe(0);
    FakeWS.all[1].open();
    expect(rig.opens).toBe(1);
    rig.sock.dispose();
  });

  it("reconnectNow() on an OPEN socket keeps it (ping probe only)", () => {
    const rig = sockRig();
    rig.sock.connect();
    last().open();
    rig.sock.reconnectNow();
    vi.advanceTimersByTime(1000);
    expect(FakeWS.all).toHaveLength(1);
    rig.sock.dispose();
  });
});
