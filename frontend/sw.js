// Offline shell only. API calls (cross-origin) are never touched.
const CACHE = "pocket-agent-v9";
const SHELL = ["./", "index.html", "app.js", "style.css", "config.js", "manifest.webmanifest", "icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin) return;
  // network first so updates show up right away; fall back to cache when offline
  e.respondWith(fetch(e.request, { cache: "no-cache" }).then((r) => {
    const copy = r.clone();
    caches.open(CACHE).then((c) => c.put(e.request, copy));
    return r;
  }).catch(() => caches.match(e.request).then((m) => m || caches.match("index.html"))));
});

// ---- push notifications (approval needed / run finished while the app is closed or in the background)
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { title: "Pocket Agent", body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "Pocket Agent", {
    body: d.body || "", tag: d.tag || undefined, renotify: !!d.tag, data: { sid: d.sid || "", world: d.world || "" }, icon: "icon-192.png", badge: "icon-192.png",
  }));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const sid = (e.notification.data && e.notification.data.sid) || "", world = (e.notification.data && e.notification.data.world) || "";
  e.waitUntil((async () => {
    const all = await clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of all) { await c.focus(); c.postMessage(world ? { type: "open-world", world } : { type: "open-chat", sid }); return; }
    await clients.openWindow("./" + (world ? "#world=" + world : sid ? "#chat=" + sid : ""));
  })());
});
