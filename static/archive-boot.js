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
  const hintKey = 'ccc-archive-response-at:' + windowName;
  let started = false, skipped = false, finishRecent, recentController, recentStart, recentTimer;
  let snapshot, baseAvailable = false;
  const promise = new Promise(resolve => { finishRecent = resolve; });
  const startRecent = () => {
    if (started || skipped) return;
    started = true;
    clearTimeout(recentTimer);
    const controller = recentController = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);
    const start = recentStart = performance.now();
    if (snapshot) snapshot.recentStart = start;
    window.fetch(url, {signal:controller.signal}).then(response => {
      response._cccArchiveTiming = {start,headers:performance.now()};
      finishRecent(response);
    }).catch(() => finishRecent(null)).finally(() => clearTimeout(timeout));
  };
  // This tiny hint schedules reads only. Readiness still requires the actual
  // persisted body and a current server response, never the hint or disk alone.
  let savedHint = false;
  try {
    const at = Number(localStorage.getItem(hintKey));
    const age = Date.now() - at;
    savedHint = at > 0 && age >= 0 && age < 86400000;
  } catch (_) {}
  // Once the complete base is usable, let the active loader decide whether
  // it has a saved preview to paint. The full request also releases this
  // fallback if it cannot validate the base.
  if (savedHint) recentTimer = setTimeout(() => { if (!baseAvailable) startRecent(); }, 1000);
  else startRecent();
  window.__cccArchiveBootstrap = {url, promise};
  // Keep one complete response only as a conditional-request base. It never
  // makes the archive ready without a current server response. Start disk I/O
  // alongside the recent-row request, before the application initializes.
  if (!window.indexedDB) { startRecent(); return; }
  try { if (localStorage.getItem('ccc-conv-cache') === '0') { startRecent(); return; } } catch (_) { startRecent(); return; }
  const fullUrl = url.replace('&initial=1', '');
  const maxBytes = 32 * 1024 * 1024;
  const maxPatchBytes = 1024 * 1024;
  const maxPatchRows = 256;
  const rowId = row => row?.session_id || row?.id;
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
        let savedHeader;
        const meta = store.get('header');
        meta.onerror = () => resolveHeader(null);
        meta.onsuccess = () => {
          savedHeader = validHeader(meta.result) ? meta.result : null;
          resolveHeader(savedHeader);
        };
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
          const hasPatch = typeof savedHeader?.patchJson === 'string';
          if (hasPatch && (savedHeader.baseAt !== value?.at || savedHeader.baseDelta !== value?.delta
              || savedHeader.baseAt > savedHeader.at || 2 * savedHeader.patchJson.length > maxPatchBytes)) {
            finish(null); return;
          }
          // The latest header dates a patched snapshot. Its base may have
          // been written much earlier, when the last full response arrived.
          if (validHeader(hasPatch ? {...value, at: savedHeader.at} : value) && Array.isArray(value.rows)
            && value.rows.length <= 30000
            && value.rows.every(row => row && typeof row === 'object' && !Array.isArray(row)
              && typeof rowId(row) === 'string')) {
            if (hasPatch) {
              try {
                const {patchJson, baseAt, baseDelta, ...metadata} = savedHeader;
                const patch = JSON.parse(patchJson);
                const byId = new Map(value.rows.map(row => [rowId(row), row]));
                for (const row of patch.changes) byId.set(rowId(row), row);
                const rows = patch.ids.map(id => byId.get(id));
                value = rows.length <= 30000 && rows.every(row => typeof rowId(row) === 'string')
                  ? {...metadata, rows} : null;
              } catch (_) { value = null; }
            }
            finish(value);
          } else {
            finish(null);
          }
        };
      } catch (_) { finish(null); }
    });
  })();
  read.then(value => {
    baseAvailable = !!value;
    if (!baseAvailable) startRecent();
  });
  let pending = null, scheduled = false;
  const save = (value, delta = null) => {
    try { if (localStorage.getItem('ccc-conv-cache') === '0') return; } catch (_) { return; }
    if (value.url !== fullUrl || !Array.isArray(value.rows) || value.rows.length > 30000) return;
    pending = {value, delta};
    if (scheduled) return;
    scheduled = true;
    const run = async () => {
      scheduled = false;
      const {value, delta} = pending; pending = null;
      try {
        const db = await open();
        if (!db) return;
        const tx = db.transaction('snapshot', 'readwrite');
        tx.onerror = () => {};
        const {rows, ...metadata} = value;
        const store = tx.objectStore('snapshot');
        const at = Date.now();
        const markSaved = () => {
          tx.oncomplete = () => {
            try { localStorage.setItem(hintKey, String(at)); } catch (_) {}
          };
        };
        const writeFull = () => {
          const rowsJson = JSON.stringify(rows);
          const bytes = 2 * rowsJson.length;
          if (bytes > maxBytes) return;
          store.put({...metadata, rowsJson, bytes, at}, 'last');
          store.put({...metadata, bytes, at}, 'header');
          markSaved();
        };
        if (!delta?.from || !value.delta || delta.changes.length > maxPatchRows) {
          writeFull();
          return;
        }
        // Compare and update within the same write transaction. If another
        // tab saved a different base, this delta cannot describe its rows.
        const head = store.get('header');
        head.onsuccess = () => {
          const old = head.result;
          if (validHeader(old) && old.delta === delta.from) {
            try {
              const previous = old.patchJson ? JSON.parse(old.patchJson) : null;
              const changes = new Map((previous?.changes || []).map(row => [rowId(row), row]));
              for (const row of delta.changes) changes.set(rowId(row), row);
              const present = new Set(delta.ids);
              const changedRows = [...changes.values()].filter(row => present.has(rowId(row)));
              if (changedRows.length > maxPatchRows) { writeFull(); return; }
              const patchJson = JSON.stringify({ids: delta.ids, changes: changedRows});
              const baseBytes = old.baseBytes || old.bytes;
              const bytes = baseBytes + 2 * patchJson.length;
              if (bytes <= maxBytes && 2 * patchJson.length <= maxPatchBytes) {
                const baseAt = old.baseAt ?? old.at;
                const baseDelta = old.baseDelta ?? old.delta;
                store.put({...metadata, patchJson, bytes, baseBytes, baseAt, baseDelta, at}, 'header');
                markSaved();
                return;
              }
            } catch (_) {}
          }
          // A coalesced save may skip a predecessor. Keep a complete base
          // when the currently committed token cannot anchor the new delta.
          try { writeFull(); } catch (_) {}
        };
      } catch (_) {}
    };
    const arm = () => window.requestIdleCallback ? window.requestIdleCallback(run, {timeout:2000}) : setTimeout(run, 0);
    if (window.__cccArchiveSettled) arm();
    else window.addEventListener('ccc:archive-settled', arm, {once:true});
  };
  header.then(value => { if (!value) startRecent(); });
  snapshot = {read, save, url:fullUrl, recentStart, startRecent,
    cancelRecent() {
      snapshot.recentCancelled = true;
      recentController?.abort();
      if (!started) { skipped = true; clearTimeout(recentTimer); finishRecent(null); }
    }};
  snapshot.fetch = header.then(async metadata => {
    const value = metadata || await read;
    // Without a persisted base, start a fresh complete read during boot too.
    // The recent-row request remains independent; either validated response
    // can win without waiting for the large application to start its fetch.
    if (!value) startRecent();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);
    const start = performance.now();
    let body = null;
    try {
      let target = value?.delta ? fullUrl + '&since=' + encodeURIComponent(value.delta) : fullUrl;
      const savedOrder = value?.delta && /^[0-9a-f]{64}$/.test(value.order) ? value.order : '';
      if (savedOrder) target += '&saved_order=' + savedOrder;
      const response = await window.fetch(target, {signal:controller.signal,
        headers:value?.etag ? {'If-None-Match':value.etag} : {}});
      response._cccArchiveTiming = {start,headers:performance.now()};
      if (response.ok && response.status !== 304) {
        // Drain the early response while the application initializes. Leaving
        // it unread behind the preview paint can stall a large full reply.
        body = response._cccArchiveParsedBody = response.json();
        body.finally(() => clearTimeout(timeout)).catch(() => {});
      }
      const base = await read;
      // Conditional validation still requires the exact persisted body.
      // An unconditional full response needs no saved base.
      if (value && (!base || base.etag !== value.etag || base.delta !== value.delta
          || base.at !== value.at || (savedOrder && base.order !== savedOrder))) {
        startRecent(); return null;
      }
      if (savedOrder) response._cccArchiveSavedOrder = savedOrder;
      if (!started && (response.status === 304 || response.ok)) {
        let ready = response.status === 304;
        if (!ready) {
          try { ready = Array.isArray((await body)?.conversations); } catch (_) {}
        }
        // The timer may have started the preview while the body was pending.
        if (ready && !started) {
          skipped = true;
          snapshot.skippedRecent = true;
          clearTimeout(recentTimer);
          finishRecent(null);
        } else if (!ready) startRecent();
      } else if (!response.ok && response.status !== 304) startRecent();
      return response;
    } catch (_) { startRecent(); return null; }
    finally { if (!body) clearTimeout(timeout); }
  });
  window.__cccArchiveSnapshot = snapshot;
})();
