// A home-screen app on iOS resumes the page it already had instead of reloading,
// so a new deploy never reaches it. Each time the app comes back to the
// foreground, compare the bundle the server now serves with the one running and
// reload when they differ.
const BUNDLE_RE = /\/assets\/index-[\w-]+\.js/;

function runningBundle(): string | null {
  for (const s of Array.from(document.scripts)) {
    const m = s.src.match(BUNDLE_RE);
    if (m) return m[0];
  }
  return null;           // dev server: nothing to compare
}

async function check() {
  const mine = runningBundle();
  if (!mine) return;
  try {
    const html = await (await fetch("/", { cache: "no-store" })).text();
    const live = html.match(BUNDLE_RE)?.[0];
    if (live && live !== mine) location.reload();
  } catch { /* offline: try again next time */ }
}

export function watchForUpdates() {
  document.addEventListener("visibilitychange", () => { if (!document.hidden) void check(); });
  window.addEventListener("pageshow", (e) => { if (e.persisted) void check(); });
}
