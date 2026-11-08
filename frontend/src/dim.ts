// Ghost text: Claude Code draws its suggested next prompt in the input box as
// SGR 2 (dim) in the default colour. xterm renders dim as the foreground at 50%
// alpha, which on this dark theme reads as real typed text. Any dim run that
// keeps the default foreground is restyled here as a dark grey italic, so a
// suggestion can't be mistaken for input. Dim text with an explicit colour
// (herdr's own chrome, coloured TUI text) is left alone.

const GHOST_FG = "38;2;88;96;112";   // #586070 on the #04070e background
const SGR_RE = /\x1b\[([0-9;:]*)m/g;

// SGR state that matters here, tracked across sequences and chunks.
let dim = false;
let fgDefault = true;
let italic = false;
let ghost = false;   // our restyle is in effect

function apply(params: string): string {
  const p = params === "" ? ["0"] : params.split(";");
  for (let i = 0; i < p.length; i++) {
    const n = p[i].includes(":") ? Number(p[i].split(":")[0]) : Number(p[i] || "0");
    if (n === 38 || n === 48 || n === 58) {
      if (!p[i].includes(":")) {
        const kind = Number(p[i + 1]);
        i += kind === 2 ? 4 : kind === 5 ? 2 : 1;   // skip the colour's own params
      }
      if (n === 38) fgDefault = false;
      continue;
    }
    if (n === 0) { dim = false; fgDefault = true; italic = false; }
    else if (n === 2) dim = true;
    else if (n === 22) dim = false;                 // also clears bold
    else if (n === 3) italic = true;
    else if (n === 23) italic = false;
    else if (n === 39) fgDefault = true;
    else if ((n >= 30 && n <= 37) || (n >= 90 && n <= 97)) fgDefault = false;
  }
  const was = ghost;
  ghost = dim && fgDefault;
  if (ghost) return `\x1b[${params};3;${GHOST_FG}m`;
  if (!was) return `\x1b[${params}m`;
  // Leaving a ghost run without a full reset: undo what we added.
  return `\x1b[${params}m` + (italic ? "" : "\x1b[23m") + (fgDefault ? "\x1b[39m" : "");
}

let carry = "";   // an SGR sequence split across WebSocket frames

export function restyleGhost(chunk: string): string {
  let s = carry + chunk;
  carry = "";
  const tail = s.match(/\x1b(\[[0-9;:]*)?$/);
  if (tail) { carry = tail[0]; s = s.slice(0, -carry.length); }
  return s.replace(SGR_RE, (_m, params: string) => apply(params));
}

// A fresh herdr client starts from default attributes.
export function resetGhost() {
  dim = false; fgDefault = true; italic = false; ghost = false; carry = "";
}
