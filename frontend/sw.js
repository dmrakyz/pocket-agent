// Offline shell only. API calls (cross-origin) are never touched.
const CACHE = "pocket-agent-v2";
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
