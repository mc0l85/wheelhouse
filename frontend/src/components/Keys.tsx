// The keys iOS/Android soft keyboards lack (Esc, Tab, Ctrl, Alt, arrows), quick
// combos (^C, the herdr prefix ^B…) and mobile-awkward symbols. Rendered inside
// the Controls popover and, when pinned, as a bottom tray.
//
// Every button keeps xterm's hidden textarea focused: preventDefault on
// pointerdown/mousedown (NOT touchstart — that kills the synthesized click),
// tabIndex -1, act on click. Otherwise iOS drops the soft keyboard and the
// viewport change forces a refit + herdr redraw.
import type { MutableRefObject } from "react";
import type { TermApi } from "../hooks/useHerdrTerminal";

export const noBlur = {
  onPointerDown: (e: React.PointerEvent) => e.preventDefault(),
  onMouseDown: (e: React.MouseEvent) => e.preventDefault(),
  tabIndex: -1,
};

type Api = MutableRefObject<TermApi | null>;
// [label, caption, sequence]
type Key = [string, string, string];

// Shown on every device. Ctrl+Esc / Ctrl+Enter are grabbed by the desktop OS or
// encoded as bare Esc / CR by xterm.js, so they go out CSI-u (herdr's crossterm
// parses that and re-encodes per pane). Alt+M is the ESC-prefixed legacy form.
const SHORTCUTS: Key[] = [
  ["Ctrl+C", "interrupt", "\x03"],
  ["Ctrl+Enter", "", "\x1b[13;5u"],
  ["Esc", "", "\x1b"],
  ["Ctrl+Esc", "", "\x1b[27;5u"],
  ["Alt+M", "", "\x1bm"],
];
const KEYS: Key[] = [
  ["Tab", "", "\t"],
  ["Newline", "Alt+Enter", "\x1b\r"],
];
const ARROWS: Key[] = [
  ["←", "", "\x1b[D"], ["↑", "", "\x1b[A"], ["↓", "", "\x1b[B"], ["→", "", "\x1b[C"],
];
const COMBOS: Key[] = [
  ["Ctrl+B", "herdr prefix", "\x02"],
  ["Ctrl+D", "end input", "\x04"],
  ["Ctrl+L", "clear", "\x0c"],
  ["Ctrl+Z", "suspend", "\x1a"],
  ["Ctrl+R", "search history", "\x12"],
];
const SYMS = ["|", "~", "/", "\\", "-", "`"];

export interface Mods { ctrl: boolean; alt: boolean; }

function KeyBtn({ api, k: [label, cap, seq] }: { api: Api; k: Key }) {
  return (
    <button {...noBlur} className="kb kl" onClick={() => api.current?.type(seq)}>
      <span className="kl-k">{label}</span>
      {cap && <span className="kl-c">{cap}</span>}
    </button>
  );
}

function Grid({ api, keys, cls = "" }: { api: Api; keys: Key[]; cls?: string }) {
  return (
    <div className={`key-grid labeled ${cls}`}>
      {keys.map((k) => <KeyBtn key={k[0]} api={api} k={k} />)}
    </div>
  );
}

export function ShortcutRow({ api }: { api: Api }) {
  return <Grid api={api} keys={SHORTCUTS} />;
}

function syncMods(api: Api, setMods: (m: Mods) => void) {
  const a = api.current?.armed();
  if (a) setMods(a);
}

interface KeysProps { api: Api; mods: Mods; setMods: (m: Mods) => void; }

export function KeyRow({ api, mods, setMods }: KeysProps) {
  const a = () => api.current;
  return (
    <div className="key-grid labeled">
      {KEYS.map((k) => <KeyBtn key={k[0]} api={api} k={k} />)}
      <button {...noBlur} className={`kb kl ${mods.ctrl ? "on" : ""}`}
        onClick={() => { a()?.toggleCtrl(); syncMods(api, setMods); }}>
        <span className="kl-k">Ctrl</span><span className="kl-c">hold for next key</span></button>
      <button {...noBlur} className={`kb kl ${mods.alt ? "on" : ""}`}
        onClick={() => { a()?.toggleAlt(); syncMods(api, setMods); }}>
        <span className="kl-k">Alt</span><span className="kl-c">hold for next key</span></button>
      <button {...noBlur} className="kb kl" onClick={() => a()?.focus()}>
        <span className="kl-k">Keyboard</span><span className="kl-c">show</span></button>
    </div>
  );
}

export function ArrowRow({ api }: { api: Api }) {
  return <Grid api={api} keys={ARROWS} cls="arrows" />;
}

export function ComboRow({ api }: { api: Api }) {
  return <Grid api={api} keys={COMBOS} />;
}

export function SymbolRow({ api }: { api: Api }) {
  return (
    <div className="key-grid labeled syms">
      {SYMS.map((s) => (
        <button {...noBlur} key={s} className="kb kl" onClick={() => api.current?.type(s)}>
          <span className="kl-k">{s}</span></button>
      ))}
    </div>
  );
}
