// A controllable WebSocket stand-in for terminalSocket.ts tests (locked acceptance helper).
import { vi } from "vitest";
import { createTerminalSocket, type TerminalSocket } from "../../../src/hooks/terminalSocket";
import type { ConnInfo } from "../../../src/types/term";

export class FakeWS {
  static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
  static all: FakeWS[] = [];
  readyState = 0;
  binaryType = "blob";
  closedByClient = false;
  sent: unknown[] = [];
  onopen: ((e: unknown) => void) | null = null;
  onclose: ((e: unknown) => void) | null = null;
  onmessage: ((e: unknown) => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  constructor(public url: string) { FakeWS.all.push(this); }
  send(d: unknown) { this.sent.push(d); }
  close() { this.closedByClient = true; this.readyState = 3; }
  open() { this.readyState = 1; this.onopen?.({}); }
  serverClose(code: number, reason = "") { this.readyState = 3; this.onclose?.({ code, reason, wasClean: code !== 1006 }); }
}

export interface SockRig { sock: TerminalSocket; conn: ConnInfo[]; opens: number; lost: number }

export function sockRig(): SockRig {
  FakeWS.all = [];
  vi.stubGlobal("WebSocket", FakeWS);
  const rig = { conn: [] as ConnInfo[], opens: 0, lost: 0 } as SockRig;
  rig.sock = createTerminalSocket({
    beforeConnect: () => {},
    url: () => "ws://test/api/ws/terminal?rows=24&cols=80",
    onConn: (i) => rig.conn.push(i),
    onData: () => {},
    onReset: () => {},
    onOpen: () => { rig.opens++; },
    onLost: () => { rig.lost++; },
  });
  return rig;
}

export const last = () => FakeWS.all[FakeWS.all.length - 1];
