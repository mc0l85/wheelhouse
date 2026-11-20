// Hard refresh: re-fetch the page past every cache, then reload.
// index.html is served no-cache and the assets are content-hashed, so once the
// browser holds a fresh index.html the reload pulls the current bundle. The
// service worker caches nothing, but we ask it to update so a new sw.js lands too.
export async function hardRefresh(
  win: Pick<Window, "location"> = window,
  doFetch: typeof fetch = fetch,
  sw: Pick<ServiceWorkerContainer, "getRegistrations"> | undefined =
    typeof navigator !== "undefined" ? navigator.serviceWorker : undefined,
): Promise<void> {
  try {
    const regs = (await sw?.getRegistrations()) ?? [];
    await Promise.all(regs.map((r) => r.update().catch(() => {})));
  } catch { /* no service worker support */ }
  try { await doFetch(win.location.href, { cache: "reload", credentials: "same-origin" }); }
  catch { /* offline — reload anyway and let the browser show what it can */ }
  win.location.reload();
}
