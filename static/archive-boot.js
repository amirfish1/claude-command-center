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

  const wideOrderSpanQuery = '&wide_order_spans=1';

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
      // The wide_order_spans capability advertises this decoder limit.
      if (d.ids.length || !savedOrder || d.base_order !== savedOrder
          || !Array.isArray(d.order_spans) || d.order_spans.length > 4096
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

  function _decodeArchiveHistoryLayer(raw, expectedWindow) {
    if (raw.history_layer !== 1) return null;
    let base, patch;
    try {
      base = _decodeArchiveRowLayouts(raw.base);
      patch = _decodeArchiveRowLayouts(raw.patch);
    } catch (_) { return null; }
    if (!Array.isArray(base?.conversations)
        || !['all', '7d'].includes(base.window) || (expectedWindow && base.window !== expectedWindow)
        || base.initial || base.delta
        || patch?.window !== base.window || patch.delta !== true || !base.delta_token
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
    _decodeArchiveRowLayouts, _mergeArchiveListDelta, _decodeArchiveHistoryLayer, wideOrderSpanQuery,
  });

  // CCC-679: sidebar list reads go to the conversation-reader process first.
  // Its own GIL keeps them out of the dashboard's queue; a declined (503) or
  // failed read is retried on the dashboard exactly as before. The server
  // injects the port; the conversation-open probe can supply it later.
  // Keep the browser's own fetch from boot; later wrappers add dashboard-only
  // behavior. Hosts without fetch at boot (tests, embeds) resolve it late.
  const bootFetch = typeof window.fetch === 'function' ? window.fetch.bind(window) : null;
  const nativeFetch = (...args) => (bootFetch || window.fetch)(...args);
  const listDirect = { downUntil: 0 };
  function listDirectBase() {
    if (Date.now() < listDirect.downUntil) return null;
    try { if (localStorage.getItem('ccc-list-direct') === '0') return null; } catch (_) {}
    if (window.__cccListDirectBase) return window.__cccListDirectBase;
    const port = window.__CCC_LIST_READER_PORT__;
    if (!port) return null;
    const loc = window.location;
    const base = (loc.protocol === 'https:' ? 'https://' : 'http://')
      + loc.hostname.replace(/^([^\[].*:.*)$/, '[$1]') + ':' + port;
    return base === loc.origin ? null : base;
  }
  // CCC-681: the reader port is a second origin with its own connection.
  // Through the tailnet proxy that connection can wedge while the
  // dashboard's stays healthy; a direct read then waited out the caller's
  // whole 45 s abort (no headers) or trickled a 330 KB body for 24 s. A
  // cold reader build can also legitimately take several seconds, and that
  // work must stay off the dashboard. So a read that makes no progress for
  // DIRECT_PATIENCE_MS pings the reader on the same origin: a live reader
  // keeps the read.
  // CCC-694: a silent ping does not prove the reader connection is the
  // problem: on a slow client link both origins crawl and the ping, queued
  // behind the read, times out too. Aborting then threw the direct read's
  // progress away and paid for a second full read (10-22 s stalls). So a
  // stalled read now hedges: the dashboard read starts alongside it and
  // whichever completes first wins; the other is cancelled. A live reader
  // whose read still makes no progress for DIRECT_HEDGE_MS is hedged too:
  // past the 5-9 s cold builds, waiting longer only adds to the stall.
  const DIRECT_PATIENCE_MS = 2000, DIRECT_PING_MS = 1500, DIRECT_HEDGE_MS = 8000,
    DIRECT_MAX_WAIT_MS = 20000;
  async function directAlive(base) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), DIRECT_PING_MS);
    try {
      const response = await nativeFetch(base + '/api/conv-reader/ping', {
        signal: controller.signal, credentials: 'include', mode: 'cors', cache: 'no-store'});
      return !!response.ok;
    } catch (_) {
      return false;
    } finally {
      clearTimeout(timer);
    }
  }
  async function readDirectBody(response, progress) {
    const reader = response.body.getReader();
    const chunks = [];
    let size = 0;
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      progress(value.byteLength);
      chunks.push(value);
      size += value.byteLength;
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return bytes;
  }
  // A buffered copy keeps the source URL (callers tell direct reads apart by
  // it), status, headers and the time its headers arrived.
  function bufferedCopy(response, bytes, headersAt) {
    const copy = new Response(bytes, {status: response.status,
      statusText: response.statusText, headers: response.headers});
    Object.defineProperty(copy, 'url', {value: response.url});
    copy._cccHeadersAt = headersAt;
    return copy;
  }
  window.__cccListFetch = function (url, init, viaDashboard) {
    const dashboard = (dashInit = init) => (viaDashboard || window.fetch)(url, dashInit);
    const base = typeof url === 'string' && url.startsWith('/api/conversations/list?') ? listDirectBase() : null;
    if (!base) return dashboard();
    const outer = init && init.signal;
    const attempt = new AbortController();
    const relay = () => attempt.abort(outer.reason);
    if (outer) {
      if (outer.aborted) attempt.abort(outer.reason);
      else outer.addEventListener('abort', relay, {once: true});
    }
    // Direct-leg timeline for the list_read beacon (CCC-694): when headers
    // and bytes arrived, how many pings went unanswered, whether and when
    // the dashboard hedge started and which read won.
    const t0 = performance.now();
    const leg = {ms: 0, headers_ms: null, bytes: 0, pings: 0, ping_fails: 0,
      hedge_ms: null, end: '', winner: 'direct'};
    const finishLeg = (response, end, winner) => {
      leg.ms = Math.round(performance.now() - t0);
      leg.end = end;
      leg.winner = winner;
      if (response && typeof response === 'object') {
        try { response._cccDirectLeg = leg; } catch (_) {}
      }
      return response;
    };
    let hedge = null, hedgeController = null;
    const startHedge = () => {
      if (hedge) return;
      leg.hedge_ms = Math.round(performance.now() - t0);
      hedgeController = new AbortController();
      const relayHedge = () => hedgeController.abort(outer.reason);
      if (outer) outer.addEventListener('abort', relayHedge, {once: true});
      const hedgeInit = Object.assign({}, init || {}, {signal: hedgeController.signal});
      hedge = (async () => {
        try {
          const response = await dashboard(hedgeInit);
          // Compare complete reads: buffer an ordinary dashboard body too.
          if (response && response.ok && response.status !== 304 && response.body
              && typeof response.arrayBuffer === 'function') {
            const headersAt = performance.now();
            return bufferedCopy(response, await response.arrayBuffer(), headersAt);
          }
          return response;
        } finally {
          if (outer) outer.removeEventListener('abort', relayHedge);
        }
      })();
      // The hedge is the dashboard read: a usable answer that completes
      // first wins. A failed one leaves the direct read to finish.
      hedge.then(response => {
        if (!response || !(response.ok || response.status === 304)) {
          hedgeFailed = true;
          return;
        }
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        attempt.abort();
        hedgeWon(response);
      }, () => { hedgeFailed = true; });
    };
    // Progress watchdog: headers or a body chunk reset it.
    let timer, settled = false, lastProgress = performance.now(), hedgeWon, hedgeFailed = false;
    const watch = () => {
      clearTimeout(timer);
      if (settled) return;
      timer = setTimeout(async () => {
        if (settled) return;
        const waited = performance.now() - lastProgress;
        if (hedge) {
          // Already hedged: no more pings. Only a failed hedge with a
          // direct read stalled past the limit ends the direct read; it
          // then settles on the hedge's own answer.
          if (hedgeFailed && waited >= DIRECT_MAX_WAIT_MS) attempt.abort();
          else watch();
          return;
        }
        let alive = false;
        if (waited < DIRECT_HEDGE_MS) {
          leg.pings += 1;
          alive = await directAlive(base);
          if (settled) return;
          if (!alive) leg.ping_fails += 1;
        }
        if (!alive) startHedge();
        watch();
      }, DIRECT_PATIENCE_MS);
    };
    const progress = bytes => {
      lastProgress = performance.now();
      if (bytes) leg.bytes += bytes;
      watch();
    };
    const options = Object.assign({}, init || {}, { signal: attempt.signal, credentials: 'include', mode: 'cors' });
    const direct = (async () => {
      let retryMs, end;
      try {
        watch();
        const response = await nativeFetch(base + url, options);
        if (response.ok || response.status === 304) {
          const headersAt = performance.now();
          leg.headers_ms = Math.round(headersAt - t0);
          if (response.status === 304 || !response.body) {
            response._cccHeadersAt = headersAt;
            return {response};
          }
          progress();
          const bytes = await readDirectBody(response, progress);
          return {response: bufferedCopy(response, bytes, headersAt)};
        }
        try { response.body?.cancel(); } catch (_) {}
        // 503: the reader is loading or declined this read; ask again soon.
        retryMs = response.status === 503 ? 15000 : 60000;
        end = 'status-' + response.status;
      } catch (err) {
        if (outer && outer.aborted) throw err;
        retryMs = 60000;
        end = 'error';
      } finally {
        if (outer) outer.removeEventListener('abort', relay);
      }
      return {retryMs, end};
    })();
    return new Promise((resolve, reject) => {
      hedgeWon = response => {
        listDirect.downUntil = Date.now() + 60000;
        resolve(finishLeg(response, 'hedged', 'dashboard'));
      };
      direct.then(result => {
        if (settled) return;
        if (result.response) {
          settled = true;
          clearTimeout(timer);
          if (hedgeController) hedgeController.abort();
          resolve(finishLeg(result.response, 'ok', 'direct'));
          return;
        }
        // The direct read failed or was declined: the dashboard serves it.
        settled = true;
        clearTimeout(timer);
        listDirect.downUntil = Date.now() + result.retryMs;
        (hedge || dashboard()).then(
          response => resolve(finishLeg(response, result.end, 'dashboard')), reject);
      }, err => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (hedgeController) hedgeController.abort();
        reject(err);
      });
    });
  };

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
  const bootId = windowName === '7d' ? window.crypto?.randomUUID?.().replaceAll('-', '') : null;
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
    const headers = bootId && !savedHint ? {'X-CCC-Archive-Boot':bootId} : undefined;
    window.__cccListFetch(url, {signal:controller.signal, headers}).then(response => {
      response._cccArchiveTiming = {start,headers:response._cccHeadersAt ?? performance.now()};
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
  const spanPatchPrefix = 'spans:';
  // Older open tabs treat this version as unreadable and fall back to a
  // full snapshot. They must not append changes while dropping the order.
  const parseSnapshotPatch = text => JSON.parse(text.startsWith(spanPatchPrefix)
    ? text.slice(spanPatchPrefix.length) : text);
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
      return _decodeArchiveHistoryLayer(packed, windowName)?.conversations || null;
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
                const patch = parseSnapshotPatch(patchJson);
                if (Object.hasOwn(patch, 'spans')) {
                  const rows = _mergeArchiveListDelta(value.rows, {
                    ids: [], conversations: patch.changes, order_spans: patch.spans,
                    count: patch.count, base_order: value.order,
                  }, value.order);
                  finish(rows && rows.length <= maxRows ? {...metadata, rows} : null);
                  return;
                }
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
    // A refresh can arrive before the idle write of the original wire body.
    // Keep that body as the anchor of the queued patch.
    const anchor = windowName === '7d' && pending?.packed instanceof ArrayBuffer
      && !pending.delta && pending.value.delta && pending.value.delta === delta?.from
      ? pending : null;
    pending = {value, delta, packed, anchor};
    if (scheduled) return;
    scheduled = true;
    const run = async () => {
      scheduled = false;
      const {value, delta, packed, anchor} = pending; pending = null;
      try {
        const db = await open();
        if (!db) return;
        const tx = db.transaction('snapshot', 'readwrite');
        tx.onerror = () => {};
        const {rows, ...metadata} = value;
        const store = tx.objectStore('snapshot');
        const at = Date.now();
        const markSaved = (bytes, saved = metadata) => {
          tx.oncomplete = () => {
            try {
              const {etag, delta, order} = saved;
              const hint = windowName === 'all'
                ? JSON.stringify({url:fullUrl, at, bytes, etag, delta, order})
                : String(at);
              localStorage.setItem(hintKey, hint);
            } catch (_) {}
          };
        };
        const writeFull = (base = value, wire = packed) => {
          const {rows, ...metadata} = base;
          // The All feed already negotiated shared row layouts. Preserve
          // that format when a coalesced or oversized delta needs its own base.
          let encoded = wire instanceof ArrayBuffer ? wire : null;
          if (!encoded) {
            const layouts = wire || (windowName === 'all' ? packRows(rows) : null);
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
          markSaved(bytes, metadata);
        };
        if (!delta?.from || !value.delta || delta.changes.length > maxPatchRows) {
          writeFull();
          return;
        }
        // Compare and update within the same write transaction. If another
        // tab saved a different base, this delta cannot describe its rows.
        if (anchor) writeFull(anchor.value, anchor.packed);
        const head = store.get('header');
        head.onsuccess = () => {
          const old = head.result;
          if (validHeader(old) && old.delta === delta.from
              && (delta.ids !== null || old.order === metadata.order)) {
            try {
              const previous = old.patchJson ? parseSnapshotPatch(old.patchJson) : null;
              const changes = new Map((previous?.changes || []).map(row => [rowId(row), row]));
              for (const row of delta.changes) changes.set(rowId(row), row);
              // Spans are relative to the original body. A same-order patch
              // can keep them. A further reorder uses its complete ID list.
              let spanOrder = null;
              if (delta.ids === null && previous?.spans) {
                spanOrder = {spans:previous.spans, count:previous.count};
              } else if (delta.ids !== null && delta.spans && delta.baseOrder === old.order
                         && !previous?.spans && previous?.ids == null) {
                spanOrder = {spans:delta.spans, count:rows.length};
              }
              const ids = delta.ids === null ? previous?.ids ?? null : delta.ids;
              const present = ids === null ? null : new Set(ids);
              const changedRows = [...changes.values()].filter(row => !present || present.has(rowId(row)));
              if (changedRows.length > maxPatchRows) { writeFull(); return; }
              const patchJson = spanOrder
                ? spanPatchPrefix + JSON.stringify({...spanOrder, changes:changedRows})
                : JSON.stringify({ids, changes:changedRows});
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
  const parseFullResponse = async (response, conditional) => {
    if (windowName !== 'all' && (windowName !== '7d' || conditional)) return response.json();
    const bytes = await response.arrayBuffer();
    const parseAt = performance.now();
    const payload = JSON.parse(new TextDecoder().decode(bytes));
    // The list's own decode cost, kept when its wait for the application
    // is set aside (CCC-696).
    if (response._cccArchiveTiming) response._cccArchiveTiming.parse_ms = performance.now() - parseAt;
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
      if (windowName === 'all') {
        target += '&row_layouts=1&order_spans=1&history_layer=1' + wideOrderSpanQuery;
      } else if (windowName === '7d') {
        target += '&row_layouts=1&order_spans=1';
        if (!value?.delta) target += '&history_layer=1' + wideOrderSpanQuery;
      }
      const headers = value?.etag ? {'If-None-Match':value.etag} : {};
      headers['X-CCC-Abort-On-Disconnect'] = '1';
      if (bootId && !savedHint && !value) headers['X-CCC-Archive-Boot'] = bootId;
      const matchesBase = base => base && base.etag === value.etag && base.delta === value.delta
        && base.at === value.at && (!savedOrder || base.order === savedOrder);
      // Start from the committed hint while IndexedDB opens. A concurrent
      // save or cleared storage can invalidate it; stop the speculative read.
      if (earlyValidator) read.then(base => { if (!matchesBase(base)) controller.abort(); });
      const response = await window.__cccListFetch(target, {signal:controller.signal, headers});
      response._cccArchiveTiming = {start,headers:response._cccHeadersAt ?? performance.now()};
      if (response.ok && response.status !== 304) {
        // Drain the early response while the application initializes. Leaving
        // it unread behind the preview paint can stall a large full reply.
        body = response._cccArchiveParsedBody = parseFullResponse(response, !!value);
        body.finally(() => clearTimeout(timeout)).catch(() => {});
        // When the list was usable, so list_read can tell its own time from
        // waiting for the application to start (CCC-696).
        const timing = response._cccArchiveTiming;
        body.then(() => { timing.parsed = performance.now(); }, () => {});
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
