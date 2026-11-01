// Blocked-agent inbox: opened from the ⚠N badge. Every blocked pane, oldest
// first, with its last lines and one-tap answers sent straight to THAT pane
// (herdr send-keys / send-text) — no focus change, so the desktop session is
// never yanked. "Open" focuses the pane for anything the buttons can't answer.
// A reply field appears only after tapping "Reply…" (a deliberate keyboard).
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Pane } from "../api/types";
import { noBlur } from "./Keys";
import { paneName } from "./PaneSelector";

const KEYS: [string, string[]][] = [
  ["Enter", ["Enter"]], ["1", ["1"]], ["2", ["2"]], ["3", ["3"]],
  ["y", ["y"]], ["n", ["n"]], ["Esc", ["esc"]], ["↑", ["Up"]], ["↓", ["Down"]],
];

function ago(since?: number): string {
  if (!since) return "";
  const s = Math.max(0, Math.round(Date.now() / 1000 - since));
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m` : `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m`;
}

function Item({ p, writes, onOpen, flash }: {
  p: Pane; writes: boolean; onOpen: (p: Pane) => void; flash: (m: string, bad?: boolean) => void;
}) {
  const [tail, setTail] = useState("…");
  const [sent, setSent] = useState("");
  const [replying, setReplying] = useState(false);
  const [text, setText] = useState("");

  const load = () => api.paneText(p.pane_id, "visible", 14)
    .then(({ content }) => setTail(content.replace(/\s+$/g, "").split("\n").slice(-12).join("\n")))
    .catch(() => setTail("(could not read pane)"));
  useEffect(() => { void load(); }, [p.pane_id, p.state_change_seq]);   // eslint-disable-line

  // The row may vanish as soon as the agent leaves "blocked", so the toast is
  // the confirmation that survives; the row's green line covers a slow agent.
  const done = (what: string) => {
    flash(`sent ${what} → ${paneName(p)} ✓`);
    setSent(`sent ${what} ✓ ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`);
    window.setTimeout(load, 700);
  };
  const keys = async (label: string, k: string[]) => {
    try { await api.sendKeys(p.pane_id, k); done(label); }
    catch (e) { flash(`send failed: ${(e as Error).message}`, true); }
  };
  const reply = async () => {
    if (!text) return;
    try {
      await api.sendText(p.pane_id, text);
      await api.sendKeys(p.pane_id, ["Enter"]);
      done(`“${text.slice(0, 20)}”`);
      setText(""); setReplying(false);
    } catch (e) { flash(`send failed: ${(e as Error).message}`, true); }
  };

  return (
    <div className={`ib-item ${sent ? "ib-sent" : ""}`}>
      <div className="ib-head">
        <span className="sdot s-blocked" />
        <span className="ib-name">{paneName(p)}{p.terminal_title_stripped ? ` · ${p.terminal_title_stripped}` : ""}</span>
        <span className="ib-ago">{ago(p.blocked_since)}</span>
        <button {...noBlur} className="kb" onClick={() => onOpen(p)}>Open</button>
      </div>
      <pre className="ib-tail">{tail}</pre>
      {writes && (
        <div className="key-grid ib-keys">
          {KEYS.map(([l, k]) => (
            <button key={l} {...noBlur} className="kb" onClick={() => keys(l, k)}>{l}</button>
          ))}
          {!replying && <button {...noBlur} className="kb wide" onClick={() => setReplying(true)}>Reply…</button>}
        </div>
      )}
      {replying && (
        <form className="ib-reply" onSubmit={(e) => { e.preventDefault(); void reply(); }}>
          <input autoFocus value={text} onChange={(e) => setText(e.target.value)}
            placeholder="type a reply, Enter sends" enterKeyHint="send" />
          <button className="kb" type="submit">Send</button>
        </form>
      )}
      {sent && <div className="ib-ok">{sent}</div>}
    </div>
  );
}

export function Inbox({ panes, writes, onOpen, flash }: {
  panes: Pane[]; writes: boolean; onOpen: (p: Pane) => void; flash: (m: string, bad?: boolean) => void;
}) {
  const blocked = panes.filter((p) => p.agent_status === "blocked")
    .sort((a, b) => (a.blocked_since ?? 9e9) - (b.blocked_since ?? 9e9));
  return (
    <div className="pop pop-left pop-inbox" data-pop>
      <div className="pop-h warn">Blocked agents ({blocked.length})</div>
      {blocked.length === 0 && <div className="pop-hint">Nothing is blocked. ✓</div>}
      {blocked.map((p) => <Item key={p.pane_id} p={p} writes={writes} onOpen={onOpen} flash={flash} />)}
    </div>
  );
}
