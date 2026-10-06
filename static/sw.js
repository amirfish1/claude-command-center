// Minimal service worker for Claude Command Center.
//
// Why this file exists: Chrome/Edge require a registered service worker with a
// fetch handler before the "Install app" affordance becomes available. Safari's
// "Add to Dock" doesn't require a SW, but having one makes the installable
// behaviour consistent across browsers.
//
// What this file deliberately does NOT do:
//   - No caching. CCC talks to a localhost server that's serving live agent
//     state. Caching responses would show stale sessions / wrong git status.
//   - No precaching. The HTML/CSS/JS are served with no-store anyway, and CCC
//     is only ever opened when the server is running.
//
// All this SW does is satisfy the "fetch handler exists" install criterion
// with a transparent passthrough. If we ever want offline support for, say,
// a read-only morning view, this is the file to extend.
//
// Version bump strategy: increment SW_VERSION whenever this file changes
// behaviour, so the browser picks up the new worker.
const SW_VERSION = '2';

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

self.addEventListener('fetch', (event) => {
  // Transparent passthrough. Letting the request fall through to the network
  // is the right default for a localhost dashboard — there's no offline story
  // to design around.
  return;
});
