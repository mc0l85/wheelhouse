// Update check: per-browser switch, one alert per new commit, and no request without a build sha.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchUpdate, firstSighting, setUpdateCheckOn, updateCheckOn } from "../src/updateCheck";

describe("update check", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it("is on by default and the switch sticks", () => {
    expect(updateCheckOn()).toBe(true);
    setUpdateCheckOn(false);
    expect(updateCheckOn()).toBe(false);
    setUpdateCheckOn(true);
    expect(updateCheckOn()).toBe(true);
  });

  it("alerts once per newer commit", () => {
    expect(firstSighting("abc1234")).toBe(true);
    expect(firstSighting("abc1234")).toBe(false);
    expect(firstSighting("def5678")).toBe(true);
    expect(firstSighting("")).toBe(false);
  });

  it("sends the build sha to the server and makes no request without one", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ enabled: true, status: "behind", behind_by: 2 })));
    vi.stubGlobal("fetch", f);
    expect(await fetchUpdate("")).toBeNull();
    expect(f).not.toHaveBeenCalled();
    const u = await fetchUpdate("6965e7a");
    expect(f).toHaveBeenCalledWith("/api/update?sha=6965e7a");
    expect(u?.status).toBe("behind");
  });
});
