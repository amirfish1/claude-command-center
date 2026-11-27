// Minimal service worker for Claude Command Center.
//
// Why this file exists: notification clicks (static/notify.js shows them via
// registration.showNotification) land in the notificationclick handler below,
// and an installed PWA keeps a registered worker. Chromium no longer needs a
// fetch handler for "Install app".
//
// What this file deliberately does NOT do:
//   - No fetch handler (CCC-718). Even an empty passthrough listener makes
//     WebKit (iOS Safari and home-screen apps) start the worker and route the
//     page navigation plus every script and API request through it. On a slow
//     phone link that added to a dashboard boot already measured at 4-9 s.
//     Without a listener the browser goes straight to the network.
//   - No caching. CCC serves live agent state; caching responses here would
//     show stale sessions or the wrong git status.
//
// Version bump strategy: increment SW_VERSION whenever this file changes
// behaviour, so the browser picks up the new worker.
const SW_VERSION = '3';

self.addEventListener('install', (event) => {
  // Activate immediately rather than waiting for old tabs to close.
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

// Notifications shown via registration.showNotification (static/notify.js)
// land here on click: focus an open dashboard tab if there is one, else open
// a fresh one at the item's URL.
self.addEventListener('notificationclick', (event) => {
  try { event.notification.close(); } catch (_) {}
  const data = (event.notification && event.notification.data) || {};
  const target = typeof data.url === 'string' && data.url.startsWith('/')
    ? data.url
    : (data.session_id ? '/?session=' + encodeURIComponent(data.session_id) : '/');
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if (client.url && 'focus' in client) {
          return client.focus().then((c) => {
            try { if (c && c.navigate) c.navigate(target); } catch (_) {}
          }).catch(() => self.clients.openWindow(target));
        }
      }
      return self.clients.openWindow(target);
    }),
  );
});
