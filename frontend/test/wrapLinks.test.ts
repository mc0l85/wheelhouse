import { describe, expect, it } from "vitest";
import { findUrls } from "../src/wrapLinks";
import oauthRows from "./fixtures_oauth_rows.json";

// Real rows from an agy sign-in pane (herdr `pane read`, 218 columns): one
// 704-character OAuth URL drawn across four rows with no soft-wrap marks.
const PANE_W = 218;
const SIDEBAR = "  agy        menu      │";            // herdr's left column + border
// herdr keeps one blank column at the pane's right edge.
const frame = (rows: string[]) => rows.map((r) => SIDEBAR + r.padEnd(PANE_W, " ") + " ");

describe("findUrls", () => {
  const rows = frame(oauthRows as string[]);
  const first = rows.findIndex((r) => r.includes("https://accounts.google.com"));
  const full = (oauthRows as string[]).slice(first, first + 4).map((r) => r.trim()).join("");

  it("joins a URL herdr wrapped across rows, from any of its rows", () => {
    for (let y = first; y < first + 4; y++) {
      const found = findUrls(rows, y);
      expect(found).toHaveLength(1);
      expect(found[0].url).toBe(full);
      expect(found[0].url).toContain("response_type=code");
      expect(found[0].url.endsWith("state=846vSMCYq4aKzIxwrGpJvQ")).toBe(true);
      expect(found[0].pieces.map((p) => p[0])).toEqual([first, first + 1, first + 2, first + 3]);
    }
  });

  it("does not join a URL that ends before the right edge", () => {
    const r = frame(["see https://example.com/a for details", "https://other.example/b"]);
    expect(findUrls(r, 0).map((u) => u.url)).toEqual(["https://example.com/a"]);
    expect(findUrls(r, 1).map((u) => u.url)).toEqual(["https://other.example/b"]);
  });

  it("does not join onto a row indented differently", () => {
    const url = "https://example.com/" + "x".repeat(PANE_W - 20);
    const r = frame([url, "    continued-looking-text"]);
    expect(findUrls(r, 0)[0].url).toBe(url);
    expect(findUrls(r, 1)).toEqual([]);
  });

  it("joins a plain hard wrap that continues at the pane's first column", () => {
    // `cat` of an indented URL: the terminal wraps at the pane width, column 0.
    const url = "https://example.com/" + "a".repeat(300) + "&response_type=code";
    const line = "  " + url;
    const r = frame([line.slice(0, PANE_W), line.slice(PANE_W), "", "next"]);
    expect(findUrls(r, 0)[0].url).toBe(url);
    expect(findUrls(r, 1)[0].url).toBe(url);
    expect(findUrls(r, 3)).toEqual([]);
  });

  it("known limit: a full-width URL swallows the first word of a column-0 next row", () => {
    const url = "https://example.com/" + "x".repeat(PANE_W - 22);
    const r = frame(["  " + url, "plain words here"]);
    expect(findUrls(r, 0)[0].url).toBe(url + "plain");
  });

  it("strips trailing sentence punctuation", () => {
    const r = frame(["open https://example.com/x)."]);
    expect(findUrls(r, 0)[0].url).toBe("https://example.com/x");
  });

  it("still finds plain one-row URLs", () => {
    const r = frame(["a", "go to https://zerocool.example/ now", "b"]);
    expect(findUrls(r, 1).map((u) => u.url)).toEqual(["https://zerocool.example/"]);
    expect(findUrls(r, 0)).toEqual([]);
  });
});
