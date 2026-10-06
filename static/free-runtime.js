/* Free ($0) spawn runtime — the composer's Free chip and the $0 badge on
   session cards. Loaded as a plain script; exposes window.CCCFreeRuntime so
   app.js hooks stay one-liners.

   The chip is a user opt-in: when on, buildSpawnBody attaches
   runtime:"free" and the server routes the child through CCC's free router.
   When the router can't serve, the spawn refuses with a clear message —
   it never silently falls back to paid. */
(function () {
  'use strict';

  var ENGINES = ['claude', 'opencode', 'aider'];
  var LS_KEY = 'ccc.freeRuntime';
  var STATUS_TTL_MS = 30000;

  var state = {
    enabled: false,
    status: null,      // last /api/free-runtime/status payload (or null)
    statusAt: 0,
    statusInflight: null,
  };
  try { state.enabled = localStorage.getItem(LS_KEY) === '1'; } catch (_) {}

  function supports(engine) {
    return ENGINES.indexOf(String(engine || '').trim().toLowerCase()) >= 0;
  }

  function refreshStatus(force) {
    var now = Date.now();
    if (!force && state.status && now - state.statusAt < STATUS_TTL_MS) {
      return Promise.resolve(state.status);
    }
    if (state.statusInflight) return state.statusInflight;
    state.statusInflight = fetch('/api/free-runtime/status', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        state.status = (d && typeof d === 'object' && d.engines) ? d : null;
        state.statusAt = Date.now();
        return state.status;
      })
      .catch(function () { return state.status; })
      .finally(function () { state.statusInflight = null; });
    return state.statusInflight;
  }

  function engineStatus(engine) {
    var engines = state.status && state.status.engines;
    return (engines && engines[String(engine || '').trim().toLowerCase()]) || null;
  }

  function readyFor(engine) {
    var s = engineStatus(engine);
    return !!(s && s.ready);
  }

  // What buildSpawnBody should send. Empty string means "no runtime key":
  // the free choice is per-request, and a chip left on for an unsupported
  // engine stays silent rather than breaking unrelated spawns.
  function runtimeForSpawn(engine) {
    if (!state.enabled || !supports(engine)) return '';
    return 'free';
  }

  function isFreeRow(row) {
    return String((row && row.runtime) || '').trim().toLowerCase() === 'free';
  }

  function ensureStyles() {
    if (document.getElementById('ccc-free-runtime-styles')) return;
    var style = document.createElement('style');
    style.id = 'ccc-free-runtime-styles';
    style.textContent = [
      '.conv-input-context .spawn-runtime-row { display: none; }',
      '.conv-input-context.is-new-session .spawn-runtime-row {',
      '  display: inline-flex; align-items: center; gap: 4px;',
      '  font-size: 12px; color: var(--text-muted); cursor: pointer; user-select: none;',
      '}',
      '.conv-input-context .spawn-runtime-row input[type="checkbox"] { margin: 0; cursor: pointer; }',
      '.spawn-runtime-row.is-unsupported { opacity: 0.45; }',
      '.spawn-runtime-row.is-on { color: var(--accent, #7ec8a9); }',
      '.spawn-runtime-row.is-warn { color: var(--warn, #d9a24a); }',
      '.meta-runtime-free {',
      '  color: var(--accent, #7ec8a9); font-weight: 600; white-space: nowrap;',
      '}',
      '.rail-runtime-free {',
      '  color: var(--accent, #7ec8a9); font-size: 11px; font-weight: 600;',
      '  border: 1px solid var(--accent, #7ec8a9); border-radius: 999px;',
      '  padding: 0 7px; line-height: 16px; white-space: nowrap;',
      '}',
      '@media (prefers-reduced-motion: no-preference) {',
      '  .spawn-runtime-row.is-on { animation: ccc-free-chip-glow 1.2s ease-out 1; }',
      '}',
      '@keyframes ccc-free-chip-glow {',
      '  from { text-shadow: 0 0 8px currentColor; } to { text-shadow: none; }',
      '}',
    ].join('\n');
    document.head.appendChild(style);
  }

  function ensureChip() {
    var wrap = document.getElementById('spawnRuntimeRow');
    if (wrap) return wrap;
    var anchor = document.querySelector('.conv-input-context .spawn-worktree-row');
    if (!anchor || !anchor.parentNode) return null;
    wrap = document.createElement('label');
    wrap.className = 'spawn-runtime-row';
    wrap.id = 'spawnRuntimeRow';
    wrap.innerHTML = '<input type="checkbox" id="freeRuntimeToggle"> &#9889; free $0';
    anchor.parentNode.insertBefore(wrap, anchor.nextSibling);
    var input = wrap.querySelector('input');
    input.checked = state.enabled;
    input.addEventListener('change', function () {
      state.enabled = !!input.checked;
      try { localStorage.setItem(LS_KEY, state.enabled ? '1' : '0'); } catch (_) {}
      sync(getCurrentEngine());
      if (state.enabled) refreshStatus(true).then(function () { sync(getCurrentEngine()); });
    });
    return wrap;
  }

  function getCurrentEngine() {
    var sel = document.getElementById('convInputEngineSelect');
    if (sel && sel.value) return sel.value;
    try {
      return (typeof getSpawnEngine === 'function') ? getSpawnEngine() : '';
    } catch (_) { return ''; }
  }

  // Called by syncSpawnEngineDependentUi on every engine/default change.
  function sync(engine) {
    ensureStyles();
    var wrap = ensureChip();
    if (!wrap) return;
    var input = wrap.querySelector('input');
    var supported = supports(engine);
    wrap.classList.toggle('is-unsupported', !supported);
    wrap.classList.toggle('is-on', supported && state.enabled);
    if (input) input.disabled = !supported;
    var status = engineStatus(engine);
    if (!supported) {
      wrap.classList.remove('is-warn');
      wrap.title = 'The free runtime works with Claude, OpenCode and Aider.';
    } else if (status && !status.ready) {
      wrap.classList.add('is-warn');
      wrap.title = 'Free router is not running (' +
        (status.reason || 'unavailable') + '). A $0 spawn will refuse rather than bill you.';
    } else {
      wrap.classList.remove('is-warn');
      wrap.title = 'Run this session on CCC\u2019s free router - $0, not your paid plan.';
    }
    if (state.enabled) {
      refreshStatus(false).then(function () {
        var s = engineStatus(engine);
        var w = document.getElementById('spawnRuntimeRow');
        if (!w) return;
        w.classList.toggle('is-warn', !!(s && !s.ready));
        if (s && !s.ready) {
          w.title = 'Free router is not running (' +
            (s.reason || 'unavailable') + '). A $0 spawn will refuse rather than bill you.';
        }
      });
    }
  }

  // Metadata rail chip for the open session ("via: UI" gets a "$0" sibling).
  function renderRailRuntime(row) {
    var via = document.getElementById('railSpawnedVia');
    if (!via || !via.parentNode) return;
    var el = document.getElementById('railRuntime');
    if (!el) {
      el = document.createElement('span');
      el.id = 'railRuntime';
      el.className = 'rail-runtime-free';
      via.parentNode.insertBefore(el, via.nextSibling);
    }
    if (isFreeRow(row)) {
      el.hidden = false;
      el.textContent = '$0';
      el.title = 'This session runs on CCC\u2019s free router - it costs nothing.';
    } else {
      el.hidden = true;
    }
  }

  function pendingCardRuntimeBit(card) {
    var rt = String((card && card.runtime) ||
      (card && card.spawn_body && card.spawn_body.runtime) || '').trim().toLowerCase();
    return rt === 'free' ? 'free ($0)' : '';
  }

  window.CCCFreeRuntime = {
    ENGINES: ENGINES,
    supports: supports,
    sync: sync,
    refreshStatus: refreshStatus,
    readyFor: readyFor,
    runtimeForSpawn: runtimeForSpawn,
    isFreeRow: isFreeRow,
    renderRailRuntime: renderRailRuntime,
    pendingCardRuntimeBit: pendingCardRuntimeBit,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { ensureStyles(); ensureChip(); });
  } else {
    ensureStyles();
    ensureChip();
  }
})();
