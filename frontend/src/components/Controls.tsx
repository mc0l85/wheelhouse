// Controls popover: keys, copy/paste and session info. Overlay (never pushes the
// terminal), stays open across key taps so a burst of arrows costs one extra tap,
// closes on an outside tap, the trigger, or after a copy.
import { type MutableRefObject, useEffect, useState } from "react";
import { api } from "../api/client";
import type { Health } from "../api/types";
import { disablePush, enablePush, pushState, type PushState } from "../push";
import { rendererPref, setRendererPref, termStats, type TermApi } from "../hooks/useHerdrTerminal";
import { ArrowRow, ComboRow, KeyRow, type Mods, noBlur, ShortcutRow, SymbolRow } from "./Keys";

interface Props {
  api: MutableRefObject<TermApi | null>;
  health: Health | null;
  paneCount: number;
  touch: boolean;
  mods: Mods;
  setMods: (m: Mods) => void;
  pinned: boolean;
  setPinned: (v: boolean) => void;
  onCopy: (source: "visible" | "recent") => void;
  onPaste: () => void;
  flash: (msg: string, bad?: boolean) => void;
}

const PUSH_HINT: Record<PushState, string> = {
  "unsupported": "this browser can't receive push notifications",
  "needs-install": "iPhone/iPad: Share › Add to Home Screen, then open it from there",
  "no-worker": "the service worker didn't start — close the app and open it again",
  "denied": "blocked in settings — allow notifications for this site/app",
  "off": "get a notification when an agent is blocked, even with the app closed",
  "on": "on — you'll be notified when an agent is blocked",
};

function NotifyRow({ flash }: { flash: Props["flash"] }) {
  const [st, setSt] = useState<PushState | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { pushState().then(setSt).catch(() => setSt("unsupported")); }, []);
  const run = async (f: () => Promise<PushState>) => {
    setBusy(true);
    try { setSt(await f()); } catch (e) { flash(`notifications: ${(e as Error).message}`, true); }
    setBusy(false);
  };
  const test = async () => {
    try {
      // Delayed: iOS may not show a banner for the app that is open on screen.
      const r = await api.pushTest(5);
      flash(r.scheduled ? "test in 5 s — lock the phone or go to the Home Screen now" : "no device is subscribed", !r.scheduled);
    } catch (e) { flash(`test failed: ${(e as Error).message}`, true); }
  };
  return (
    <>
      <div className="pop-h">Notifications</div>
      {!st && <div className="pop-hint">checking…</div>}
      <div className="key-grid">
        {st === "off" && <button {...noBlur} className="kb wide" disabled={busy} onClick={() => run(enablePush)}>🔔 Turn on</button>}
        {st === "on" && <>
          <button {...noBlur} className="kb wide on" disabled={busy} onClick={() => run(disablePush)}>🔔 On — turn off</button>
          <button {...noBlur} className="kb wide" onClick={test}>Send test (5 s)</button>
        </>}
      </div>
      {st && <div className="pop-hint">{PUSH_HINT[st]}</div>}
    </>
  );
}

export function Controls(p: Props) {
  const writes = !!p.health?.writes_enabled;
  const [fs, setFs] = useState(() => p.api.current?.fontSize() ?? 13);
  const step = (d: number) => { const n = p.api.current?.fontStep(d); if (n) setFs(n); };
  return (
    <div className="pop pop-right" data-pop {...noBlur}>
      {writes && (
        <>
          <div className="pop-h">Shortcuts</div>
          <ShortcutRow api={p.api} />
        </>
      )}
      {p.touch && writes && (
        <>
          <div className="pop-h">Keys</div>
          <KeyRow api={p.api} mods={p.mods} setMods={p.setMods} />
          <div className="pop-h">Arrows</div>
          <ArrowRow api={p.api} />
          <div className="pop-h">Ctrl combos</div>
          <ComboRow api={p.api} />
          <div className="pop-h">Symbols</div>
          <SymbolRow api={p.api} />
        </>
      )}
      <div className="pop-h">Text size</div>
      <div className="key-grid font-row">
        <button {...noBlur} className="kb" disabled={fs <= 9} onClick={() => step(-1)}>A−</button>
        <span className="font-size">{fs}px</span>
        <button {...noBlur} className="kb" disabled={fs >= 24} onClick={() => step(1)}>A+</button>
        <span className="bar-grow" />
        <button {...noBlur} className="kb wide" title="canvas = solid borders/scrollbars; DOM = fallback (reloads)"
          onClick={() => setRendererPref(rendererPref() === "canvas" ? "dom" : "canvas")}>
          Renderer: {rendererPref() === "canvas" ? "Canvas" : "DOM"}</button>
      </div>
      <div className="pop-h">Copy</div>
      <div className="key-grid">
        <button {...noBlur} className="kb wide" onClick={() => p.onCopy("visible")}>📋 Copy pane</button>
        <button {...noBlur} className="kb wide" onClick={() => p.onCopy("recent")}>📋 + scrollback</button>
        {writes && <button {...noBlur} className="kb wide" onClick={p.onPaste}>Paste</button>}
      </div>
      <div className="pop-hint">drag = select + copy · Shift-drag = raw select · Ctrl+Shift+V = paste</div>
      <NotifyRow flash={p.flash} />
      <div className="pop-h">Session</div>
      <div className="pop-info">
        <span>session: {p.health?.session ?? "?"}</span>
        <span>{p.paneCount} panes</span>
        <span className={p.health?.server_running ? "ok" : "bad"}>
          server {p.health?.server_running ? "running" : "down"}
        </span>
        <span>{writes ? "writes ON" : "read-only"}</span>
        <span className="dim">PTY resizes: {termStats.ptyResizes}</span>
      </div>
      {p.touch && writes && (
        <div className="key-grid">
          <button {...noBlur} className={`kb wide ${p.pinned ? "on" : ""}`}
            onClick={() => p.setPinned(!p.pinned)}>📌 {p.pinned ? "Unpin" : "Pin"} keys tray</button>
        </div>
      )}
    </div>
  );
}
