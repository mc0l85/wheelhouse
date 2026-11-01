// Service worker: push notifications only (no offline caching — a terminal
// with no server is useless). A blocked-agent push shows a notification and
// sets the app badge; tapping it opens (or focuses) the app on the inbox.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { body: e.data && e.data.text() }; }
  const jobs = [self.registration.showNotification(d.title || "herdr web", {
    body: d.body || "",
    tag: d.pane_id ? `blocked-${d.pane_id}` : "herdr-web",   // one per pane, replaced not stacked
    renotify: !!d.pane_id,
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    data: { pane_id: d.pane_id || null },
  })];
  if (typeof d.blocked === "number" && self.navigator.setAppBadge) {
    jobs.push(self.navigator.setAppBadge(d.blocked).catch(() => {}));
  }
  e.waitUntil(Promise.all(jobs));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const pane = e.notification.data && e.notification.data.pane_id;
  const url = pane ? `/?inbox=${encodeURIComponent(pane)}` : "/";
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      if (new URL(w.url).origin === self.location.origin) {
        await w.focus();
        w.postMessage({ type: "open-inbox", pane_id: pane });
        return;
      }
    }
    await self.clients.openWindow(url);
  })());
});
