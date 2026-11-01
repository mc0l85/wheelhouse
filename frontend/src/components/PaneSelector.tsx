// Pane selector popover: every herdr pane, grouped workspace → tab in herdr's own
// (sidebar) order, with a "Needs attention" group of blocked panes on top.
// Selecting a pane focuses it in herdr; the terminal keeps focus throughout.
import type { Pane, Tree } from "../api/types";
import { noBlur } from "./Keys";

const base = (p?: string) => (p ? p.replace(/\/+$/, "").split("/").pop() : "") || "";

export function paneName(p: Pane): string {
  return p.agent || (p.terminal_title_stripped ?? "").slice(0, 28) || "shell";
}

function Dot({ s }: { s?: string }) {
  return <span className={`sdot s-${s ?? "unknown"}`} />;
}

function Row({ p, onPick }: { p: Pane; onPick: (p: Pane) => void }) {
  const title = p.agent ? p.terminal_title_stripped : "";
  return (
    <button {...noBlur} className={`pane-pick ${p.focused ? "cur" : ""}`}
      aria-current={p.focused || undefined} onClick={() => onPick(p)}>
      <Dot s={p.agent_status} />
      <span className="pp-main">
        <span className="pp-name">{paneName(p)}{title ? ` · ${title}` : ""}</span>
        <span className="pp-sub">{base(p.foreground_cwd || p.cwd)}</span>
      </span>
    </button>
  );
}

export function PaneSelector({ tree, onPick }: { tree: Tree | null; onPick: (p: Pane) => void }) {
  if (!tree) return <div className="pop pop-left" data-pop {...noBlur}><div className="pop-h">loading…</div></div>;
  const all = tree.workspaces.flatMap((w) => w.tabs.flatMap((t) => t.panes));
  const blocked = all.filter((p) => p.agent_status === "blocked");
  return (
    <div className="pop pop-left" data-pop {...noBlur}>
      {blocked.length > 0 && (
        <>
          <div className="pop-h warn">Needs attention ({blocked.length})</div>
          {blocked.map((p) => <Row key={`b-${p.pane_id}`} p={p} onPick={onPick} />)}
        </>
      )}
      {tree.workspaces.map((w) => (
        <div key={w.workspace_id}>
          {tree.workspaces.length > 1 && <div className="pop-h">{w.label}</div>}
          {w.tabs.map((t) => (
            <div key={t.tab_id} className="pp-tab">
              <div className="pp-tabh"><Dot s={t.agent_status} />{t.label}</div>
              {t.panes.map((p) => <Row key={p.pane_id} p={p} onPick={onPick} />)}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
