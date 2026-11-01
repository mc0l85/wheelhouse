// Tiny typed fetch wrapper over the herdr-web REST API (same origin as the app).
// The live terminal and event stream are WebSockets, in hooks/.
import type { Health, Layout, Pane, SessionTree, Tree } from "./types";

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => j<Health>("/api/health"),
  workspaces: () => j<SessionTree>("/api/workspaces"),
  panes: () => j<{ panes: Pane[] }>("/api/panes"),
  tree: () => j<Tree>("/api/tree"),
  // Clean text of ONE pane (no sidebar) — for the "Copy pane" button.
  paneText: (id: string, source = "visible", lines = 400) =>
    j<{ content: string }>(
      `/api/panes/${encodeURIComponent(id)}/read?source=${source}&lines=${lines}&format=text`),
  layout: (paneId: string) => j<Layout>(`/api/layout/${encodeURIComponent(paneId)}`),

  // gated mutations — backend returns 403 unless HERDRWEB_ENABLE_WRITES=1
  focusPane: (paneId: string) =>
    j(`/api/panes/${encodeURIComponent(paneId)}/focus`, { method: "POST" }),
  zoomPane: (paneId: string) =>
    j(`/api/panes/${encodeURIComponent(paneId)}/zoom`, { method: "POST", body: JSON.stringify({ mode: "toggle" }) }),
  sendText: (paneId: string, text: string) =>
    j(`/api/panes/${encodeURIComponent(paneId)}/send-text`, { method: "POST", body: JSON.stringify({ text }) }),
  run: (paneId: string, command: string) =>
    j(`/api/panes/${encodeURIComponent(paneId)}/run`, { method: "POST", body: JSON.stringify({ command }) }),
  sendKeys: (paneId: string, keys: string[]) =>
    j(`/api/panes/${encodeURIComponent(paneId)}/send-keys`, { method: "POST", body: JSON.stringify({ keys }) }),

  // Web Push (not gated by writes: it only tells this browser about blocked agents)
  pushKey: () => j<{ public_key: string; subscribers: number }>("/api/push/key"),
  pushSubscribe: (subscription: PushSubscriptionJSON) =>
    j("/api/push/subscribe", { method: "POST", body: JSON.stringify(subscription) }),
  pushUnsubscribe: (endpoint: string) =>
    j("/api/push/unsubscribe", { method: "POST", body: JSON.stringify({ endpoint }) }),
  pushTest: () => j<{ sent: number; dropped: number; failed: number }>("/api/push/test", { method: "POST" }),
};
