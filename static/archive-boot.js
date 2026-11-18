// Start the dashboard's recent-row read while its larger scripts load.
(function () {
  'use strict';
  if (window.self !== window.top) return;
  const params = new URLSearchParams(location.search);
  const app = params.get('ccc_app');
  if (params.get('ccc_popout') === 'conversation' || params.get('popout') === 'conversation'
      || (app && app !== 'sessions' && app !== 'queues')) return;
  let windowName = '7d';
  try {
    const saved = localStorage.getItem('ccc-archive-window');
    if (['8h', '1d', '7d', 'all'].includes(saved)) windowName = saved;
  } catch (_) {}
  const url = '/api/conversations/list?window=' + windowName + '&stale_ok=1&initial=1';
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 45000);
  const start = performance.now();
  const promise = window.fetch(url, {signal: controller.signal})
    .then(response => {
      response._cccArchiveTiming = {start, headers: performance.now()};
      return response;
    })
    .catch(() => null).finally(() => clearTimeout(timeout));
  window.__cccArchiveBootstrap = {url, promise};
  // Keep one complete response only as a conditional-request base. It never
  // makes the archive ready without a current server response. Start disk I/O
  // alongside the recent-row request, before the application initializes.
  if (!window.indexedDB) return;
  try { if (localStorage.getItem('ccc-conv-cache') === '0') return; } catch (_) { return; }
  const fullUrl = url.replace('&initial=1', '');
  const maxBytes = 32 * 1024 * 1024;
  let dbPromise;
  const open = () => dbPromise || (dbPromise = new Promise(resolve => {
    let done = false;
    const finish = db => { if (done) { db?.close(); return; } done = true; clearTimeout(timer); resolve(db); };
    const timer = setTimeout(() => finish(null), 500);
    try {
      const request = window.indexedDB.open('ccc-archive-response', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('snapshot');
      request.onerror = request.onblocked = () => finish(null);
      request.onsuccess = () => {
        request.result.onversionchange = () => request.result.close();
        finish(request.result);
      };
    } catch (_) { finish(null); }
  }));
  const validHeader = value => {
    const age = value && Date.now() - value.at;
    return value?.url === fullUrl && Number.isFinite(age) && age >= 0 && age < 86400000
      && value.bytes > 0 && value.bytes <= maxBytes
      && ((typeof value.etag === 'string' && value.etag)
        || (typeof value.delta === 'string' && value.delta));
  };
  let resolveHeader;
  const header = new Promise(resolve => { resolveHeader = resolve; });
  const read = (async () => {
    const db = await open();
    if (!db) { resolveHeader(null); return null; }
    return new Promise(resolve => {
      let done = false;
      const finish = value => { if (!done) { done = true; clearTimeout(timer); resolveHeader(null); resolve(value); } };
      const timer = setTimeout(() => finish(null), 500);
      try {
        // Both reads share one transaction. A tiny validation header can start
        // the request while IndexedDB clones the complete rows onto the page.
        const store = db.transaction('snapshot').objectStore('snapshot');
        const meta = store.get('header');
        meta.onerror = () => resolveHeader(null);
        meta.onsuccess = () => resolveHeader(validHeader(meta.result) ? meta.result : null);
        const request = store.get('last');
        request.onerror = () => finish(null);
        request.onsuccess = () => {
          let value = request.result;
          if (!validHeader(value)) { finish(null); return; }
          // Text avoids IndexedDB's recursive structured clone of every row.
          // Older object snapshots remain readable during a rolling update.
          if (typeof value.rowsJson === 'string') {
            try {
              const {rowsJson, ...metadata} = value;
              value = rowsJson.length * 2 <= maxBytes
                ? {...metadata, rows:JSON.parse(rowsJson)} : null;
            } catch (_) { value = null; }
          }
          finish(validHeader(value) && Array.isArray(value.rows)
            && value.rows.length <= 30000
            && value.rows.every(row => row && typeof row === 'object' && !Array.isArray(row)
              && typeof (row.session_id || row.id) === 'string') ? value : null);
        };
      } catch (_) { finish(null); }
    });
  })();
  let pending = null, scheduled = false;
  const save = value => {
    try { if (localStorage.getItem('ccc-conv-cache') === '0') return; } catch (_) { return; }
    if (value.url !== fullUrl || !Array.isArray(value.rows) || value.rows.length > 30000) return;
    pending = value;
    if (scheduled) return;
    scheduled = true;
    const run = async () => {
      scheduled = false;
      const value = pending; pending = null;
      try {
        const rowsJson = JSON.stringify(value.rows);
        const bytes = 2 * rowsJson.length;
        if (bytes > maxBytes) return;
        const db = await open();
        if (!db) return;
        const tx = db.transaction('snapshot', 'readwrite');
        tx.onerror = () => {};
        const {rows, ...metadata} = value;
        const record = {...metadata, rowsJson, bytes, at: Date.now()};
        const store = tx.objectStore('snapshot');
        store.put(record, 'last');
        store.put({url:record.url, bytes, at:record.at, etag:record.etag, delta:record.delta}, 'header');
      } catch (_) {}
    };
    const arm = () => window.requestIdleCallback ? window.requestIdleCallback(run, {timeout:2000}) : setTimeout(run, 0);
    if (window.__cccArchiveSettled) arm();
    else window.addEventListener('ccc:archive-settled', arm, {once:true});
  };
  const snapshot = {read, save, url:fullUrl, recentStart:start,
    cancelRecent() { snapshot.recentCancelled = true; controller.abort(); }};
  snapshot.fetch = header.then(async metadata => {
    const value = metadata || await read;
    if (!value) return null;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);
    const start = performance.now();
    let body = null;
    try {
      const target = value.delta ? fullUrl + '&since=' + encodeURIComponent(value.delta) : fullUrl;
      const response = await window.fetch(target, {signal:controller.signal,
        headers:value.etag ? {'If-None-Match':value.etag} : {}});
      response._cccArchiveTiming = {start,headers:performance.now()};
      if (response.ok && response.status !== 304) {
        // Drain the early response while the application initializes. Leaving
        // it unread behind the preview paint can stall a large full reply.
        body = response._cccArchiveParsedBody = response.json();
        body.finally(() => clearTimeout(timeout)).catch(() => {});
      }
      const base = await read;
      // Never apply validation for a different or damaged persisted body.
      if (!base || base.etag !== value.etag || base.delta !== value.delta || base.at !== value.at) return null;
      return response;
    } catch (_) { return null; }
    finally { if (!body) clearTimeout(timeout); }
  });
  window.__cccArchiveSnapshot = snapshot;
})();
