// Hard refresh: fetch the page past the cache, update the service worker, then reload — in that order.
import { describe, expect, it, vi } from "vitest";
import { hardRefresh } from "../src/hardRefresh";

describe("hard refresh", () => {
  it("re-fetches with cache:reload and updates the worker before reloading", async () => {
    const order: string[] = [];
    const win = { location: { href: "https://wh.example/", reload: vi.fn(() => order.push("reload")) } };
    const doFetch = vi.fn(async (_u: RequestInfo | URL, init?: RequestInit) => {
      order.push(`fetch:${init?.cache}`);
      return new Response("");
    });
    const reg = { update: vi.fn(async () => { order.push("sw-update"); }) };
    const sw = { getRegistrations: vi.fn(async () => [reg]) };
    await hardRefresh(win as never, doFetch as never, sw as never);
    expect(doFetch).toHaveBeenCalledWith("https://wh.example/", expect.objectContaining({ cache: "reload" }));
    expect(order).toEqual(["sw-update", "fetch:reload", "reload"]);
  });

  it("still reloads when offline and with no service worker", async () => {
    const win = { location: { href: "https://wh.example/", reload: vi.fn() } };
    const doFetch = vi.fn(async () => { throw new TypeError("offline"); });
    await hardRefresh(win as never, doFetch as never, undefined);
    expect(win.location.reload).toHaveBeenCalledOnce();
  });
});
