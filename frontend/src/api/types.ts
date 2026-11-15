// Mirrors the FastAPI contract (backend/app/models.py) + herdr CLI shapes.

export type AgentStatus = "idle" | "working" | "blocked" | "done" | "unknown";

export interface AgentSession {
  agent?: string;
  kind?: string;
  source?: string;
  value?: string;
}

export interface Pane {
  pane_id: string;
  terminal_id?: string;
  tab_id?: string;
  workspace_id?: string;
  agent?: string;
  agent_status?: AgentStatus;
  agent_session?: AgentSession;
  cwd?: string;
  foreground_cwd?: string;
  focused?: boolean;
  revision?: number;
  terminal_title_stripped?: string;
  terminal_title?: string;
  label?: string;
  scroll?: PaneScroll;
  state_change_seq?: number;
  blocked_since?: number;      // epoch s, set by the backend event hub
}

export interface PaneScroll {
  offset_from_bottom: number;
  max_offset_from_bottom: number;
  viewport_rows: number;
}

export interface CellRect { x: number; y: number; width: number; height: number; }
export interface Layout {
  tab_id?: string; zoomed?: boolean;
  panes: { pane_id: string; focused?: boolean; rect: CellRect }[];
}

export interface Health {
  ok: boolean;
  herdr_bin: string;
  session: string;
  server_running: boolean;
  writes_enabled: boolean;
  pane_count?: number;
  error?: string;
}

// session.json shape (workspace tree) — only the fields the UI reads.
export interface SessionPane { cwd?: string; agent_session?: AgentSession; }
export interface SessionTab {
  custom_name?: string | null;
  panes: Record<string, SessionPane>;
  focused?: number;
}
export interface Workspace {
  id: string;
  custom_name?: string;
  identity_cwd?: string;
  tabs: SessionTab[];
  active_tab?: number;
}
export interface SessionTree { version: number; workspaces: Workspace[]; active?: number; }

// GET /api/tree — live workspace→tab→pane tree in herdr's own (sidebar) order.
export interface TreeTab {
  tab_id: string; label: string; number?: number; focused?: boolean;
  agent_status?: AgentStatus; panes: Pane[];
}
export interface TreeWorkspace {
  workspace_id: string; label: string; focused?: boolean; tabs: TreeTab[];
}
export interface Tree { workspaces: TreeWorkspace[]; }
