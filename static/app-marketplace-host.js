/* Trusted host. Never expose a general-purpose fetch or an API URL to an app. */
(function () {
  'use strict';
  const config = JSON.parse(document.getElementById('app-config').textContent);
  const frame = document.getElementById('app-frame');
  const methods = new Set(['sessions.read', 'queues.read']);
  let busy = false;
  let lastCall = 0;
  window.addEventListener('message', async function (event) {
    if (event.source !== frame.contentWindow || event.origin !== 'null') return;
    const message = event.data;
    if (!message || typeof message !== 'object' || message.type !== 'ccc.app.call' ||
        typeof message.requestId !== 'string' || message.requestId.length > 80) return;
    const reply = function (data) {
      // Opaque origins require '*'; the recipient is this specific frame only.
      frame.contentWindow.postMessage({type: 'ccc.app.result', requestId: message.requestId, ...data}, '*');
    };
    if (!methods.has(message.method) || !config.permissions.includes(message.method)) {
      reply({ok: false, error: 'Permission denied'});
      return;
    }
    if (busy || Date.now() - lastCall < 1000) {
      reply({ok: false, error: 'Please wait before requesting another snapshot'});
      return;
    }
    busy = true;
    lastCall = Date.now();
    try {
      const response = await fetch('/api/app-marketplace/bridge', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: config.id, generation: config.generation, method: message.method})
      });
      reply(await response.json());
    } catch (_) {
      reply({ok: false, error: 'App bridge unavailable'});
    } finally {
      busy = false;
    }
  });
  frame.src = '/marketplace-content/' + encodeURIComponent(config.id) + '/index.html';
})();
