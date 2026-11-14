// Recover same-origin API reads across service stops and proxy startup gaps.
(function (root) {
  'use strict';
  const nativeFetch = root.fetch.bind(root);
  const recoveryMs = 60000;
  let active = 0;
  let notice;
  let message = '';
  let terminalMessage = '';
  const retryMessage = 'Server restarting or unavailable; retrying…';

  function render(text) {
    message = text;
    if (!document.body) {
      document.addEventListener('DOMContentLoaded', () => render(message), { once: true });
      return;
    }
    if (!notice) {
      notice = document.createElement('div');
      notice.id = 'cccRestartTransportStatus';
      notice.className = 'ccc-restart-transport-status';
      notice.setAttribute('role', 'status');
      notice.setAttribute('aria-live', 'polite');
      document.body.appendChild(notice);
    }
    notice.textContent = text;
    if (terminalMessage) {
      const dismiss = document.createElement('button');
      dismiss.type = 'button';
      dismiss.textContent = 'Dismiss';
      dismiss.setAttribute('aria-label', 'Dismiss connection notice');
      dismiss.addEventListener('click', () => {
        terminalMessage = '';
        render(active ? retryMessage : '');
      });
      notice.appendChild(dismiss);
    }
    notice.hidden = !text;
  }

  function wait(ms, signal) {
    return new Promise((resolve, reject) => {
      const abort = () => {
        clearTimeout(timer);
        signal.removeEventListener('abort', abort);
        reject(signal.reason || new DOMException('Aborted', 'AbortError'));
      };
      const timer = setTimeout(() => {
        if (signal) signal.removeEventListener('abort', abort);
        resolve();
      }, ms);
      if (signal) {
        signal.addEventListener('abort', abort, { once: true });
        if (signal.aborted) abort();
      }
    });
  }

  root.fetch = async function restartAwareFetch(input, init) {
    // Endpoint-specific helpers may own durable receipts and recovery copy.
    if (init && init.cccRetryHandled === true) return nativeFetch(input, init);
    let url;
    try { url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url, root.location.href); }
    catch (_) { return nativeFetch(input, init); }
    if (url.origin !== root.location.origin || !url.pathname.startsWith('/api/')) {
      return nativeFetch(input, init);
    }
    const method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    const read = method === 'GET' || method === 'HEAD';
    // Best-effort diagnostic beacons do not represent an unsaved user action.
    if (!read && ['/api/client-log', '/api/free-runtime/tts-log'].includes(url.pathname)) {
      return nativeFetch(input, init);
    }
    const signal = (init && init.signal) || (input && input.signal);
    // Keep a fresh request for each attempt, including Request-object bodies.
    // Never replay a write after an ambiguous network/proxy failure.
    const template = input instanceof Request ? new Request(input, init) : new Request(url, init);
    const deadline = Date.now() + recoveryMs;
    let recovering = false;
    let failure = '';
    let attempt = 0;
    try {
      while (true) {
        let response;
        let error;
        try { response = await nativeFetch(template.clone()); }
        catch (err) {
          if ((signal && signal.aborted) || err.name !== 'TypeError') throw err;
          error = err;
        }
        const transient = error || (response && [502, 503, 504].includes(response.status));
        if (!transient) {
          if (recovering && active === 1 && !terminalMessage) render('');
          return response;
        }
        const notStarted = response && response.status === 503
          && response.headers.get('X-CCC-Restarting') === '1';
        if (!read && !notStarted) {
          failure = 'Connection interrupted. Check whether your change was saved before retrying.';
          terminalMessage = failure;
          render(failure);
          if (error) throw new Error(failure, { cause: error });
          return response;
        }
        if (Date.now() >= deadline) {
          failure = 'Server still unavailable. Please try again when it reconnects.';
          terminalMessage = failure;
          render(failure);
          if (error) throw new Error(failure, { cause: error });
          return response;
        }
        if (!recovering) { recovering = true; active += 1; }
        render(terminalMessage || retryMessage);
        // Release error bodies before waiting; don't retain a proxy connection.
        if (response && response.body) await response.body.cancel().catch(() => {});
        const delay = Math.min(2000, 300 * (2 ** Math.min(attempt++, 3)));
        await wait(Math.min(delay, Math.max(0, deadline - Date.now())), signal);
      }
    } finally {
      if (recovering) {
        active -= 1;
        if (!active && !terminalMessage) render('');
      }
    }
  };
})(window);
