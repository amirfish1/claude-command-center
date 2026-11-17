// All ticket composers share the same retry and confirmation contract.
(function (root) {
  'use strict';
  const memory = new Map();
  const storageKey = 'ccc-ticket-submissions';
  function read() {
    try {
      const value = JSON.parse(root.sessionStorage.getItem(storageKey) || '{}');
      return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    }
    catch (_) { return {}; }
  }
  function remember(signature, key) {
    const entries = read();
    if (key) { entries[signature] = key; memory.set(signature, key); }
    else { delete entries[signature]; memory.delete(signature); }
    try { root.sessionStorage.setItem(storageKey, JSON.stringify(entries)); } catch (_) {}
  }
  async function submit(payload) {
    const signature = JSON.stringify(payload);
    const key = memory.get(signature) || read()[signature]
      || (root.crypto && typeof root.crypto.randomUUID === 'function' ? root.crypto.randomUUID()
        : 'ticket-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2)
          + '-' + Math.random().toString(36).slice(2));
    remember(signature, key);
    const body = JSON.stringify(Object.assign({}, payload, {idempotency_key: key}));
    const note = String(payload.note || payload.text || '').replace(/\s+/g, ' ').trim();
    const preview = note.length > 80 ? note.slice(0, 80) + '…' : note;
    const context = (payload.project || 'default queue') + ' (“' + preview + '”)';
    const started = Date.now();
    const elapsed = () => Math.round((Date.now() - started) / 1000) + 's';
    let reason = 'Connection lost';
    let failures = 0;
    for (let attempt = 0; attempt < 40 && failures < 4; attempt++) {
      if (attempt) {
        // Retry quietly; transient save checks should not produce notifications.
        await new Promise(resolve => setTimeout(resolve, 1000 * Math.min(attempt, 3)));
      }
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch('/api/ux-fixes/enqueue', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body, signal: controller.signal, cccRetryHandled: true,
        });
        const data = await response.json().catch(() => null);
        if (response.ok && data && data.ok && data.item && data.item.ref) {
          remember(signature, null);
          return data;
        }
        reason = (data && data.error) || (response.status === 202 ? 'Queue has not confirmed the save yet'
          : response.ok ? 'Server reply did not include a ticket reference' : 'HTTP ' + response.status);
        if (response.status === 202 && data && data.pending) continue;
        failures++;
        if (response.status >= 400 && response.status < 500) {
          remember(signature, null);
          throw Object.assign(new Error(reason), {rejected: true});
        }
      } catch (error) {
        if (error.rejected) throw error;
        failures++;
        reason = controller.signal.aborted || error.name === 'AbortError' || error.name === 'TimeoutError'
          ? 'No confirmation within 10 seconds' : (error.message || reason);
      } finally { clearTimeout(timer); }
    }
    throw Object.assign(new Error('Ticket confirmation pending in ' + context + ' after ' + elapsed()
      + '. Saving is not confirmed. Last check: ' + reason
      + '. Check the queue or retry this same ticket safely.'), {pending: true});
  }
  root.cccSubmitQueueTicket = submit;
})(window);
