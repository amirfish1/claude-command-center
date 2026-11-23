// Start the dashboard's recent-row read while its larger scripts load.
(function () {
  'use strict';
  function _decodeArchiveRowLayouts(payload) {
    if (!payload || !Object.hasOwn(payload, 'row_layouts')) return payload;
    const {row_layouts: layouts, conversations, ...metadata} = payload;
    if (!Array.isArray(layouts) || !Array.isArray(conversations)
        || layouts.some(keys => !Array.isArray(keys) || keys.some(key => typeof key !== 'string' || key === '__proto__'))) {
      throw new Error('Invalid archive row layouts');
    }
    const templates = new Map();
    const rows = conversations.map(encoded => {
      const keys = Array.isArray(encoded) && Number.isInteger(encoded[0]) && layouts[encoded[0]];
      if (!keys || !Array.isArray(encoded[1]) || keys.length !== encoded[1].length) {
        throw new Error('Invalid archive row');
      }
      // Copy an established layout before setting values. Large layouts
      // otherwise grow a new property's table one field at a time per row.
      let template = templates.get(keys);
      if (!template) {
        template = Object.fromEntries(keys.map(key => [key, null]));
        templates.set(keys, template);
      }
      const row = Object.assign({}, template);
      for (let i = 0; i < keys.length; i++) row[keys[i]] = encoded[1][i];
      return row;
    });
    return {...metadata, conversations:rows};
  }

  function _mergeArchiveListDelta(prev, d, savedOrder) {
    if (!Array.isArray(prev) || !Array.isArray(d.ids) || !Array.isArray(d.conversations)) return null;
    const sameOrder = d.same_order === true;
    if (sameOrder && (!savedOrder || d.order_token !== savedOrder || d.count !== prev.length)) return null;
    const byId = new Map();
    for (const row of prev) {
      const id = row && (row.session_id || row.id);
      if (id) byId.set(String(id), row);
    }
    if (sameOrder && byId.size !== prev.length) return null;
    for (const row of d.conversations) {
      const id = row && (row.session_id || row.id);
      if (!id || (sameOrder && !byId.has(String(id)))) return null;
      byId.set(String(id), row);
    }
    if (sameOrder) return [...byId.values()];
    if (Object.hasOwn(d, 'order_spans')) {
      // Offsets only describe the exact pre-delta array validated by the server.
      if (d.ids.length || !savedOrder || d.base_order !== savedOrder
          || !Array.isArray(d.order_spans) || d.order_spans.length > 256
          || !Number.isSafeInteger(d.count) || d.count < 0
          || d.count > prev.length + d.conversations.length) return null;
      const rows = [], seen = new Set();
      const append = id => {
        if (!byId.has(id) || seen.has(id) || rows.length >= d.count) return false;
        seen.add(id);
        rows.push(byId.get(id));
        return true;
      };
      for (const span of d.order_spans) {
        if (typeof span === 'string') {
          if (!append(span)) return null;
          continue;
        }
        if (!Array.isArray(span) || span.length !== 2) return null;
        const [start, count] = span;
        if (!Number.isSafeInteger(start) || !Number.isSafeInteger(count)
            || start < 0 || count < 1 || start + count > prev.length) return null;
        for (let i = start; i < start + count; i++) {
          const row = prev[i];
          if (!row || !append(String(row.session_id || row.id || ''))) return null;
        }
      }
      return rows.length === d.count ? rows : null;
    }
    const out = new Array(d.ids.length);
    for (let i = 0; i < d.ids.length; i++) {
      const row = byId.get(String(d.ids[i]));
      if (!row) return null;
      out[i] = row;
    }
    return out;
  }

  function _decodeArchiveHistoryLayer(raw) {
    if (raw.history_layer !== 1) return null;
    let base, patch;
    try {
      base = _decodeArchiveRowLayouts(raw.base);
      patch = _decodeArchiveRowLayouts(raw.patch);
    } catch (_) { return null; }
    if (!Array.isArray(base?.conversations)
        || base.window !== 'all' || base.initial || base.delta
        || patch?.window !== 'all' || patch.delta !== true || !base.delta_token
        || patch.since !== base.delta_token || !patch.delta_token
        || base.count !== base.conversations.length
        || !Array.isArray(patch.conversations) || !Array.isArray(patch.ids)
        || !/^[0-9a-f]{64}$/.test(base.order_token || '')
        || !/^[0-9a-f]{64}$/.test(patch.order_token || '')) {
      return null;
    }
    const validIds = (items, key) => {
      const seen = new Set();
      for (const item of items) {
        const id = key(item);
        if (typeof id !== 'string' || !id || seen.has(id)) return false;
        seen.add(id);
      }
      return true;
    };
    const rowId = row => row?.session_id || row?.id;
    if (!validIds(base.conversations, rowId) || !validIds(patch.conversations, rowId)
        || !validIds(patch.ids, id => id)) return null;
    const rows = _mergeArchiveListDelta(base.conversations, patch, base.order_token);
    if (!rows || rows.length !== patch.count) return null;
    // The layer is self-contained. Keep the current token and materialized
    // complete rows for conditional reads and IndexedDB, like an ordinary 200.
    const {delta, since, ids, order_spans, base_order, same_order, ...metadata} = patch;
    return {...metadata, conversations: rows};
  }

  window.CCCArchiveWire = Object.freeze({
    _decodeArchiveRowLayouts, _mergeArchiveListDelta, _decodeArchiveHistoryLayer,
  });

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
    if (recentTimer !== undefined) clearTimeout(recentTimer);
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
  let savedHint = false, validatorHint = null;
  try {
    const stored = localStorage.getItem(hintKey);
    if (windowName === 'all' && stored?.startsWith('{') && stored.length <= 2048) {
      validatorHint = JSON.parse(stored);
    }
    const at = validatorHint?.at ?? Number(stored);
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
  const blobMinBytes = 1024 * 1024;
  const canReadBlob = typeof Blob === 'function' && typeof Blob.prototype.arrayBuffer === 'function';
  const maxPatchBytes = 1024 * 1024;
  const maxPatchRows = 256;
  const maxRows = 30000;
  const rowId = row => row?.session_id || row?.id;
  const packRows = rows => {
    const layouts = [], slots = new Map(), encoded = [];
    for (const row of rows) {
      if (!row || typeof row !== 'object' || Array.isArray(row)) return null;
      const keys = Object.keys(row);
      const shape = JSON.stringify(keys);
      let slot = slots.get(shape);
      if (slot === undefined) {
        if (keys.includes('__proto__')) return null;
        slot = layouts.length;
        slots.set(shape, slot);
        layouts.push(keys);
      }
      encoded.push([slot, Object.values(row)]);
    }
    return [layouts, encoded];
  };
  const readPackedRows = bytes => {
    if (!(bytes instanceof ArrayBuffer) || !bytes.byteLength || bytes.byteLength > maxBytes) return null;
    const packed = JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(bytes));
    if (!packed || packed.delta || packed.initial) return null;
    if (packed.history_layer !== undefined) {
      if (packed.base?.count > maxRows || packed.patch?.count > maxRows) return null;
      return _decodeArchiveHistoryLayer(packed)?.conversations || null;
    }
    // Older snapshots store the layout pair. A complete boot response can
    // be stored directly without serializing its decoded rows again.
    const [layouts, encodedRows] = Array.isArray(packed)
      ? packed : [packed.row_layouts, packed.conversations];
    if (!Array.isArray(encodedRows) || encodedRows.length > maxRows) return null;
    return _decodeArchiveRowLayouts({row_layouts:layouts, conversations:encodedRows}).conversations;
  };
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
      const savedBodyDeadline = performance.now() + 1500;
      let timer = setTimeout(() => finish(null), 500);
      try {
        // Both reads share one transaction. A tiny validation header can start
        // the request while IndexedDB retrieves the cached body.
        const store = db.transaction('snapshot').objectStore('snapshot');
        let savedHeader;
        const meta = store.get('header');
        meta.onerror = () => resolveHeader(null);
        meta.onsuccess = () => {
          if (done) return;
          savedHeader = validHeader(meta.result) ? meta.result : null;
          if (windowName === 'all' && savedHeader) {
            // A committed header can validate while the much larger All
            // body finishes reading. A premature abort forces a full fetch.
            clearTimeout(timer);
            timer = setTimeout(() => finish(null), Math.max(0, savedBodyDeadline - performance.now()));
          }
          resolveHeader(savedHeader);
        };
        const request = store.get('last');
        request.onerror = () => finish(null);
        request.onsuccess = async () => {
          if (done) return;
          let value = request.result;
          if (!validHeader(value)) { finish(null); return; }
          // Text avoids IndexedDB's recursive structured clone of every row.
          // Older object snapshots remain readable during a rolling update.
          if (Object.hasOwn(value, 'packedRows')) {
            try {
              const {packedRows, ...metadata} = value;
              let bytes = packedRows;
              if (canReadBlob && packedRows instanceof Blob) {
                bytes = packedRows.size === value.bytes ? await packedRows.arrayBuffer() : null;
                if (done) return;
              }
              const rows = bytes?.byteLength === value.bytes ? readPackedRows(bytes) : null;
              value = rows ? {...metadata, rows} : null;
            } catch (_) { value = null; }
            if (done) return;
          } else if (typeof value.rowsJson === 'string') {
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
            && value.rows.length <= maxRows
            && value.rows.every(row => row && typeof row === 'object' && !Array.isArray(row)
              && typeof rowId(row) === 'string')) {
            if (hasPatch) {
              try {
                const {patchJson, baseAt, baseDelta, ...metadata} = savedHeader;
                const patch = JSON.parse(patchJson);
                const byId = new Map(value.rows.map(row => [rowId(row), row]));
                for (const row of patch.changes) byId.set(rowId(row), row);
                const ids = patch.ids === null ? value.rows.map(rowId) : patch.ids;
                const rows = ids.map(id => byId.get(id));
                value = rows.length <= maxRows && rows.every(row => typeof rowId(row) === 'string')
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
  const save = (value, delta = null, packed = null) => {
    try { if (localStorage.getItem('ccc-conv-cache') === '0') return; } catch (_) { return; }
    if (value.url !== fullUrl || !Array.isArray(value.rows) || value.rows.length > maxRows) return;
    pending = {value, delta, packed};
    if (scheduled) return;
    scheduled = true;
    const run = async () => {
      scheduled = false;
      const {value, delta, packed} = pending; pending = null;
      try {
        const db = await open();
        if (!db) return;
        const tx = db.transaction('snapshot', 'readwrite');
        tx.onerror = () => {};
        const {rows, ...metadata} = value;
        const store = tx.objectStore('snapshot');
        const at = Date.now();
        const markSaved = bytes => {
          tx.oncomplete = () => {
            try {
              const {etag, delta, order} = metadata;
              const hint = windowName === 'all'
                ? JSON.stringify({url:fullUrl, at, bytes, etag, delta, order})
                : String(at);
              localStorage.setItem(hintKey, hint);
            } catch (_) {}
          };
        };
        const writeFull = () => {
          // The All feed already negotiated shared row layouts. Preserve
          // that format when a coalesced or oversized delta needs its own base.
          let encoded = packed instanceof ArrayBuffer ? packed : null;
          if (!encoded) {
            const layouts = packed || (windowName === 'all' ? packRows(rows) : null);
            if (layouts) encoded = new TextEncoder().encode(JSON.stringify(layouts)).buffer;
          }
          const rowsJson = encoded ? null : JSON.stringify(rows);
          const bytes = encoded ? encoded.byteLength : 2 * rowsJson.length;
          if (bytes > maxBytes) return;
          // Large ArrayBuffers are cloned by IndexedDB. A Blob lets the
          // browser read those bytes asynchronously outside the transaction.
          const storage = encoded
            ? {packedRows:canReadBlob && bytes >= blobMinBytes ? new Blob([encoded]) : encoded}
            : {rowsJson};
          store.put({...metadata, ...storage, bytes, at}, 'last');
          store.put({...metadata, bytes, at}, 'header');
          markSaved(bytes);
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
          if (validHeader(old) && old.delta === delta.from
              && (delta.ids !== null || old.order === metadata.order)) {
            try {
              const previous = old.patchJson ? JSON.parse(old.patchJson) : null;
              const changes = new Map((previous?.changes || []).map(row => [rowId(row), row]));
              for (const row of delta.changes) changes.set(rowId(row), row);
              // An order-preserving delta can keep the base's order or the
              // last explicit patch order without writing every ID again.
              const ids = delta.ids === null ? previous?.ids ?? null : delta.ids;
              const present = ids === null ? null : new Set(ids);
              const changedRows = [...changes.values()].filter(row => !present || present.has(rowId(row)));
              if (changedRows.length > maxPatchRows) { writeFull(); return; }
              const patchJson = JSON.stringify({ids, changes: changedRows});
              const baseBytes = old.baseBytes || old.bytes;
              const bytes = baseBytes + 2 * patchJson.length;
              if (bytes <= maxBytes && 2 * patchJson.length <= maxPatchBytes) {
                const baseAt = old.baseAt ?? old.at;
                const baseDelta = old.baseDelta ?? old.delta;
                store.put({...metadata, patchJson, bytes, baseBytes, baseAt, baseDelta, at}, 'header');
                markSaved(bytes);
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
  const parseFullResponse = async response => {
    if (windowName !== 'all') return response.json();
    const bytes = await response.arrayBuffer();
    const payload = JSON.parse(new TextDecoder().decode(bytes));
    // An early response for a different view may never be consumed. Keep
    // the extra buffer only when the complete-list loader has picked it up.
    if (snapshot.consumed && !payload?.delta && !payload?.initial && (Array.isArray(payload?.row_layouts) || payload?.history_layer === 1)
        && bytes.byteLength <= maxBytes) {
      response._cccArchivePackedBytes = bytes;
    }
    return payload;
  };
  snapshot = {read, save, url:fullUrl, recentStart, startRecent,
    cancelRecent() {
      snapshot.recentCancelled = true;
      recentController?.abort();
      if (!started) { skipped = true; clearTimeout(recentTimer); finishRecent(null); }
    }};
  const earlyValidator = validHeader(validatorHint) ? validatorHint : null;
  snapshot.fetch = (async () => {
    const value = earlyValidator || await header || await read;
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
      if (windowName === 'all') target += '&row_layouts=1&order_spans=1&history_layer=1';
      const headers = value?.etag ? {'If-None-Match':value.etag} : {};
      headers['X-CCC-Abort-On-Disconnect'] = '1';
      const matchesBase = base => base && base.etag === value.etag && base.delta === value.delta
        && base.at === value.at && (!savedOrder || base.order === savedOrder);
      // Start from the committed hint while IndexedDB opens. A concurrent
      // save or cleared storage can invalidate it; stop the speculative read.
      if (earlyValidator) read.then(base => { if (!matchesBase(base)) controller.abort(); });
      const response = await window.fetch(target, {signal:controller.signal, headers});
      response._cccArchiveTiming = {start,headers:performance.now()};
      if (response.ok && response.status !== 304) {
        // Drain the early response while the application initializes. Leaving
        // it unread behind the preview paint can stall a large full reply.
        body = response._cccArchiveParsedBody = parseFullResponse(response);
        body.finally(() => clearTimeout(timeout)).catch(() => {});
      }
      const base = await read;
      // Conditional validation still requires the exact persisted body.
      // An unconditional full response needs no saved base.
      if (value && !matchesBase(base)) {
        body?.then(() => { delete response._cccArchivePackedBytes; }, () => {});
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
  })();
  window.__cccArchiveSnapshot = snapshot;
})();
