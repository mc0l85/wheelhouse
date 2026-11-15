// Clickable URLs that herdr has wrapped across screen rows.
//
// herdr draws every pane row with cursor moves, so xterm never marks a row as a
// soft-wrap of the one above (isWrapped stays false) and WebLinksAddon only ever
// sees the first row of a long URL. Clicking that row opened a truncated URL —
// an OAuth link lost everything after the first row (Google: "Required parameter
// is missing: response_type").
//
// Rule for joining: a URL piece that runs into the right edge of its row (only
// border/scrollbar glyphs, never a space, after it) continues on the next row
// when that row starts, at the URL block's left text column, with URL characters.
import type { IBufferLine, ILink, ILinkProvider, Terminal } from "@xterm/xterm";

const URL_RE = /https?:\/\/[^\s"'<>`\x01]+/g;
const URL_CHAR = /[^\s"'<>`\x01]/;
// herdr pane borders and scrollbars, which may sit after the last text column.
const EDGE = /[│┃║▏▕▐▌█▒░┆┊|]/;
const MAX_ROWS = 12;

/** Row text indexed by CELL column (wide-char tails become \x01, so offsets stay columns). */
export function rowText(line: IBufferLine | undefined, cols: number): string {
  if (!line) return "";
  let s = "";
  for (let x = 0; x < cols; x++) {
    const c = line.getCell(x);
    if (!c) { s += " "; continue; }
    if (c.getWidth() === 0) { s += "\x01"; continue; }
    const ch = c.getChars();
    s += ch === "" ? " " : ch.length > 1 ? "\x01" : ch;
  }
  return s;
}

/** Index just past the URL-char run that starts at i. */
function runEnd(row: string, i: number): number {
  while (i < row.length && URL_CHAR.test(row[i])) i++;
  return i;
}

/** True if column e is the pane's right edge: at most 3 cells follow, and they
 *  are only padding spaces or border/scrollbar glyphs (herdr keeps one blank
 *  column at a pane's right edge). */
function atRightEdge(row: string, e: number): boolean {
  const rest = row.slice(e);
  return rest.length <= 3 && [...rest].every((c) => c === " " || EDGE.test(c));
}

/** First column of the pane holding column i: just past the nearest border glyph. */
function paneLeft(row: string, i: number): number {
  let j = i;
  while (j > 0 && !EDGE.test(row[j - 1])) j--;
  return j;
}

/** Left text column of the pane block holding column i: back to the nearest
 *  border glyph (herdr's sidebar/pane edge) or row start, then past spaces. */
function blockLeft(row: string, i: number): number {
  let j = i;
  while (j > 0 && !EDGE.test(row[j - 1])) j--;
  while (j < i && row[j] === " ") j++;
  return j;
}

/** Where row continues a URL piece whose row had pane column pl and text indent
 *  il: at the indent (an app that re-indents its own wrap, like agy) or at the
 *  pane's first column (a plain hard wrap). -1 = not a continuation. */
function continuesAt(row: string | undefined, pl: number, il: number): number {
  if (row === undefined) return -1;
  for (const c of il === pl ? [il] : [il, pl]) {
    if (URL_CHAR.test(row[c] ?? " ") && paneLeft(row, c) === pl && blockLeft(row, c) === c) return c;
  }
  return -1;
}

export interface WrappedUrl {
  url: string;
  /** 0-based [row, startCol, endColExclusive] for each piece, top to bottom. */
  pieces: [number, number, number][];
}

/** All URLs (wrapped or not) that have a piece on row y. rows[i] is row i. */
export function findUrls(rows: string[], y: number): WrappedUrl[] {
  // Walk up while row r-1 wraps into row r, to find where a URL touching y begins.
  let top = y;
  for (let k = 0; k < MAX_ROWS && top > 0; k++) {
    if (!continues(rows, top - 1)) break;
    top--;
  }
  const out: WrappedUrl[] = [];
  let r = top;
  while (r <= y) {
    const row = rows[r] ?? "";
    URL_RE.lastIndex = 0;
    let m: RegExpExecArray | null;
    let next = r + 1;
    while ((m = URL_RE.exec(row))) {
      const pieces: [number, number, number][] = [[r, m.index, m.index + m[0].length]];
      let url = m[0];
      let rr = r;
      const il = blockLeft(row, m.index);
      const pl = paneLeft(row, m.index);
      while (rr - r < MAX_ROWS && atRightEdge(rows[rr] ?? "", pieces[pieces.length - 1][2])) {
        const nrow = rows[rr + 1];
        const left = continuesAt(nrow, pl, il);
        if (left < 0) break;
        const e = runEnd(nrow!, left);
        pieces.push([rr + 1, left, e]);
        url += nrow!.slice(left, e);
        rr++;
      }
      // Trailing sentence punctuation is not part of the URL.
      const trim = url.match(/[.,;:!?)\]}]+$/);
      if (trim) {
        url = url.slice(0, -trim[0].length);
        const last = pieces[pieces.length - 1];
        last[2] -= trim[0].length;
        if (last[2] <= last[1]) pieces.pop();
      }
      if (pieces.some(([pr]) => pr === y)) out.push({ url, pieces });
      next = Math.max(next, rr + 1);
    }
    r = next;
  }
  return out;
}

/** Row r ends in a URL run at its right edge and row r+1 continues it. */
function continues(rows: string[], r: number): boolean {
  const row = rows[r] ?? "";
  const trimmed = row.replace(/[ ]+$/, "");
  let e = trimmed.length;
  while (e > 0 && EDGE.test(trimmed[e - 1])) e--;
  if (e === 0 || !URL_CHAR.test(row[e - 1]) || !atRightEdge(row, e)) return false;
  let b = e;
  while (b > 0 && URL_CHAR.test(row[b - 1])) b--;
  return continuesAt(rows[r + 1], paneLeft(row, b), blockLeft(row, b)) >= 0;
}

export class WrappedLinkProvider implements ILinkProvider {
  constructor(private term: Terminal, private open: (uri: string) => void) {}

  provideLinks(y1: number, callback: (links: ILink[] | undefined) => void): void {
    const buf = this.term.buffer.active;
    const cols = this.term.cols;
    const y = y1 - 1;
    const lo = Math.max(0, y - MAX_ROWS);
    const hi = Math.min(buf.length - 1, y + MAX_ROWS);
    const rows: string[] = [];
    for (let i = 0; i <= hi; i++) rows.push(i < lo ? "" : rowText(buf.getLine(i), cols));
    const links = findUrls(rows, y).map(({ url, pieces }): ILink => {
      const [r0, s0] = pieces[0];
      const [r1, , e1] = pieces[pieces.length - 1];
      return {
        range: { start: { x: s0 + 1, y: r0 + 1 }, end: { x: e1, y: r1 + 1 } },
        text: url,
        decorations: { underline: true, pointerCursor: true },
        activate: () => this.open(url),
      };
    });
    callback(links.length ? links : undefined);
  }
}
