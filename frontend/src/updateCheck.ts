// "Is there a newer Wheelhouse on GitHub?" The server asks GitHub (cached 6 h per build); this
// file decides when to ask, remembers the per-browser on/off switch, and only alerts once per
// newer commit. Distinct from update.ts, which reloads the page after a deploy to THIS server.
import { useCallback, useEffect, useState } from "react";

export interface UpdateInfo {
  enabled: boolean;
  status?: "behind" | "current" | "unknown" | "error" | string;
  behind_by?: number;
  latest?: string;
  latest_message?: string;
  running?: string;
  repo?: string;
  url?: string;
  note?: string;
}

const PREF = "wheelhouse.updateCheck";
const SEEN = "wheelhouse.updateSeen";
const EVERY_MS = 6 * 3600 * 1000;

export const BUILD_SHA: string = typeof __BUILD_SHA__ === "string" ? __BUILD_SHA__ : "";

function get(k: string): string | null { try { return localStorage.getItem(k); } catch { return null; } }
function put(k: string, v: string) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }

export function updateCheckOn(): boolean { return get(PREF) !== "off"; }
export function setUpdateCheckOn(on: boolean) { put(PREF, on ? "on" : "off"); }

/** First time this browser sees `latest`: true once, then false (so the toast never nags). */
export function firstSighting(latest: string): boolean {
  if (!latest || get(SEEN) === latest) return false;
  put(SEEN, latest);
  return true;
}

export async function fetchUpdate(sha = BUILD_SHA): Promise<UpdateInfo | null> {
  if (!sha) return null;                       // dev server / build without a commit: nothing to compare
  const res = await fetch(`/api/update?sha=${encodeURIComponent(sha)}`);
  if (!res.ok) return null;
  return (await res.json()) as UpdateInfo;
}

/** The update state for the app: checks on load, every 6 h and on return to the foreground. */
export function useUpdateCheck(onNew: (u: UpdateInfo) => void) {
  const [on, setOn] = useState(updateCheckOn);
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const check = useCallback(async () => {
    if (!updateCheckOn()) { setInfo(null); return; }
    try {
      const u = await fetchUpdate();
      setInfo(u);
      if (u?.status === "behind" && u.latest && firstSighting(u.latest)) onNew(u);
    } catch { /* offline: next time */ }
  }, [onNew]);
  useEffect(() => {
    void check();
    const t = window.setInterval(() => void check(), EVERY_MS);
    let last = Date.now();
    const vis = () => { if (!document.hidden && Date.now() - last > EVERY_MS) { last = Date.now(); void check(); } };
    document.addEventListener("visibilitychange", vis);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", vis); };
  }, [check]);
  const toggle = useCallback((v: boolean) => {
    setUpdateCheckOn(v); setOn(v);
    if (v) void check(); else setInfo(null);
  }, [check]);
  return { on, info, toggle, available: on && info?.status === "behind" };
}
