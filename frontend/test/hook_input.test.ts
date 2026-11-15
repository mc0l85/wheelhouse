// Hook-level input wiring against a real xterm in jsdom: origin classification of xterm's onData.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { act } from "react";
import { useHerdrTerminal } from "../src/hooks/useHerdrTerminal";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

class WS {
  static all: WS[] = [];
  static OPEN = 1; static CONNECTING = 0; static CLOSING = 2; static CLOSED = 3;
  readyState = 1; binaryType = "blob"; sent: Uint8Array[] = [];
  onopen: ((e: unknown) => void) | null = null; onclose: ((e: unknown) => void) | null = null;
  onmessage: ((e: unknown) => void) | null = null; onerror: ((e: unknown) => void) | null = null;
  constructor(public url: string) { WS.all.push(this); setTimeout(() => this.onopen?.({}), 0); }
  send(d: unknown) { if (ArrayBuffer.isView(d)) this.sent.push(d as Uint8Array); }
  close() { this.readyState = 3; }
}

function Probe() {
  const { hostRef } = useHerdrTerminal("s", true, true);
  return createElement("div", { ref: hostRef, style: { width: 400, height: 300 } });
}

let root: Root, host: HTMLDivElement;
beforeEach(() => {
  WS.all = [];
  vi.stubGlobal("WebSocket", WS);
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} unobserve() {} });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
  vi.stubGlobal("requestAnimationFrame", (f: () => void) => setTimeout(f, 0));
  vi.stubGlobal("cancelAnimationFrame", (n: number) => clearTimeout(n));
  HTMLCanvasElement.prototype.getContext = (() => null) as never;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); });

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const text = (ws: WS) => ws.sent.map((b) => new TextDecoder().decode(b)).join("");

async function boot() {
  await act(async () => { root.render(createElement(Probe)); });
  await act(async () => { await sleep(80); });
  const ta = document.querySelector(".xterm-helper-textarea") as HTMLTextAreaElement;
  expect(ta).toBeTruthy();
  expect(WS.all.length).toBeGreaterThan(0);
  return { ta, ws: WS.all[WS.all.length - 1] };
}
const enter = (ta: HTMLElement) =>
  ta.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true, cancelable: true }));

describe("hook input classification (real xterm)", () => {
  it("hardware Enter mid-composition: the word once, then CR; a second Enter is not dropped", async () => {
    const { ta, ws } = await boot();
    ta.focus();
    ta.dispatchEvent(new CompositionEvent("compositionstart", { data: "", bubbles: true }));
    ta.dispatchEvent(new CompositionEvent("compositionupdate", { data: "hel", bubbles: true }));
    ta.value = "hel";
    ta.dispatchEvent(new InputEvent("input", { inputType: "insertCompositionText", data: "hel", isComposing: true, bubbles: true }));
    expect(text(ws)).toBe("hel");
    await act(async () => { await sleep(20); });   // xterm records the composition end in a timeout
    enter(ta);
    ta.dispatchEvent(new CompositionEvent("compositionend", { data: "hel", bubbles: true }));
    await act(async () => { await sleep(300); });
    expect(text(ws)).toBe("hel\r");
    enter(ta);
    await act(async () => { await sleep(50); });
    expect(text(ws)).toBe("hel\r\r");
  });

  it("plain hardware keys right after a composition are all sent", async () => {
    const { ta, ws } = await boot();
    ta.focus();
    ta.dispatchEvent(new CompositionEvent("compositionstart", { data: "", bubbles: true }));
    ta.dispatchEvent(new CompositionEvent("compositionupdate", { data: "a", bubbles: true }));
    ta.value = "a";
    ta.dispatchEvent(new InputEvent("input", { inputType: "insertCompositionText", data: "a", isComposing: true, bubbles: true }));
    ta.dispatchEvent(new CompositionEvent("compositionend", { data: "a", bubbles: true }));
    await act(async () => { await sleep(300); });
    enter(ta); enter(ta);
    await act(async () => { await sleep(50); });
    expect(text(ws)).toBe("a\r\r");
  });
});
