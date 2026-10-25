/* Job Radar service worker: offline shell, fresh jobs.json, push notifications. */
const VERSION = "jobradar-v20";
const SHELL = ["./", "index.html", "app.css", "app.js", "sync.js", "cloud.js", "account.js", "desk.js", "native.js", "manifest.webmanifest",
               "icons/icon.svg", "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin) return;

  // jobs.json: always try the network first so you see the latest run; cache is the offline fallback
  if (url.pathname.endsWith("/jobs.json")) {
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(new Request(url.pathname), copy));
          return res;
        })
        .catch(() => caches.match(new Request(url.pathname))),
    );
    return;
  }

  // App shell: serve from cache, refresh in the background
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const network = fetch(event.request)
        .then((res) => {
          if (res.ok) caches.open(VERSION).then((c) => c.put(event.request, res.clone()));
          return res;
        })
        .catch(() => cached);
      return cached || network;
    }),
  );
});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: "Job Radar", body: event.data && event.data.text() };
  }
  const options = {
    body: data.body || "",
    tag: data.tag,
    icon: "icons/icon-192.png",
    badge: "icons/badge-96.png",
    data: { job_id: data.job_id, url: data.url },
    actions: data.url ? [{ action: "apply", title: "Apply" }, { action: "open", title: "Open app" }] : [],
  };
  event.waitUntil(self.registration.showNotification(data.title || "Job Radar", options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const { job_id, url } = event.notification.data || {};
  const appUrl = self.registration.scope + (job_id ? `#job=${job_id}` : "");
  const target = event.action === "apply" && url ? url : appUrl;
  event.waitUntil((async () => {
    if (target === appUrl) {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const w of windows) {
        if (w.url.startsWith(self.registration.scope)) {
          await w.focus();
          return w.navigate(appUrl);
        }
      }
    }
    return self.clients.openWindow(target);
  })());
});
