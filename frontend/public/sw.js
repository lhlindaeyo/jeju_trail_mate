// Service worker: offline app shell + phone notifications (top banner / lock screen).
// Offline: cache the app shell; API GETs fall back to the last cached copy.
// Notifications: Web Push messages from the server, and alerts the open app hands over, both shown as system
// notifications. The same alert id is used as the tag, so an alert that arrives both ways shows once.
const SHELL = "jtm-shell-v4";
const API = "jtm-api-v1";

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(["/", "/index.html", "/manifest.webmanifest", "/icon-192.png", "/splash.webp"])));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => ![SHELL, API].includes(k)).map((k) => caches.delete(k)))));
  self.clients.claim();
});

// Dev-server internals (Vite) are never cached: they change on every edit.
const DEV_PATH = /^\/(@vite|@react-refresh|@fs|src\/|node_modules\/)/;

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin || DEV_PATH.test(url.pathname)) return; // map tiles etc. go straight to network
  if (url.pathname.startsWith("/v1/")) {
    e.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(API).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => caches.match(req).then((hit) => hit || new Response(JSON.stringify({ detail: "offline" }), { status: 503 })))
    );
    return;
  }
  e.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(SHELL).then((c) => c.put(req, copy));
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match("/index.html")))
  );
});

// ---- notifications ----------------------------------------------------------------------
function show(n) {
  const safety = n.severity === "safety";
  return self.registration.showNotification(n.title || "JEJU TRAIL MATE", {
    body: n.body || "",
    tag: n.id || undefined, // one notification per alert
    renotify: Boolean(n.id),
    requireInteraction: safety, // safety alerts stay until tapped
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    vibrate: safety ? [300, 120, 300, 120, 300] : [200],
    timestamp: Date.now(),
    data: { url: n.url || "/#brief" },
  });
}

self.addEventListener("push", (e) => {
  let n = {};
  try { n = e.data ? e.data.json() : {}; } catch { n = { body: e.data ? e.data.text() : "" }; }
  e.waitUntil(show(n));
});

// The open app passes alerts here (postMessage) so they reach the banner / lock screen while it is in the background.
self.addEventListener("message", (e) => {
  if (e.data && e.data.type === "notify") e.waitUntil(show(e.data.notification));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = new URL((e.notification.data && e.notification.data.url) || "/", self.location.origin).href;
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      const open = wins.find((w) => new URL(w.url).origin === self.location.origin);
      if (open) return open.navigate(target).then((w) => (w || open).focus()).catch(() => open.focus());
      return self.clients.openWindow(target);
    })
  );
});
