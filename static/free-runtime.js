/* Free ($0) spawn runtime — the "$0 Free" model pill and the $0 badge on
   session cards. Loaded as a plain script; exposes window.CCCFreeRuntime so
   app.js hooks stay one-liners.

   The pill is a user opt-in: when on, buildSpawnBody attaches
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
      '.ccc-free-pill {',
      '  display: inline-flex; align-items: center; gap: 5px; height: 21px;',
      '  padding: 0 8px 0 3px; border: 1px solid #2ea043; border-radius: 999px;',
      '  background: var(--surface); color: var(--text); font: inherit;',
      '  font-size: 12px; font-weight: 600; cursor: pointer; white-space: nowrap;',
      '}',
      '.ccc-free-pill-badge {',
      '  display: inline-flex; align-items: center; justify-content: center;',
      '  min-width: 16px; height: 16px; padding: 0 3px; border-radius: 999px;',
      '  background: #2ea043; color: #fff; font-size: 10px; font-weight: 700;',
      '}',
      '.ccc-free-pill.is-selected {',
      '  background: color-mix(in srgb, #2ea043 22%, var(--surface));',
      '  box-shadow: 0 0 0 1px color-mix(in srgb, #2ea043 55%, transparent);',
      '}',
      '.ccc-free-pill.is-setup { border-style: dashed; color: var(--text-muted); }',
      '.ccc-free-pill.is-setup .ccc-free-pill-badge { background: var(--text-muted); }',
      '.ccc-free-composer-label { display: none; }',
      'body.ccc-free-on:has(.conv-input-context.is-new-session) #convInputModelSelect,',
      'body.ccc-free-on:has(.conv-input-context.is-new-session) #convInputEffortSelect {',
      '  display: none !important;',
      '}',
      'body.ccc-free-on:has(.conv-input-context.is-new-session) .ccc-free-composer-label {',
      '  display: inline-flex; align-items: center; gap: 5px; padding: 0 8px; height: 26px;',
      '  border: 1px solid #2ea043; border-radius: 6px; color: var(--text);',
      '  font-size: 12px; font-weight: 600; white-space: nowrap;',
      '}',
      '.ccc-free-on .orch-tier-chip.is-selected {',
      '  border-color: var(--border); background: var(--surface); box-shadow: none;',
      '  color: var(--text-muted);',
      '}',
      '.meta-runtime-free {',
      '  color: var(--accent, #7ec8a9); font-weight: 600; white-space: nowrap;',
      '}',
      '.rail-runtime-free {',
      '  color: var(--accent, #7ec8a9); font-size: 11px; font-weight: 600;',
      '  border: 1px solid var(--accent, #7ec8a9); border-radius: 999px;',
      '  padding: 0 7px; line-height: 16px; white-space: nowrap;',
      '}',
    ].join('\n');
    document.head.appendChild(style);
  }

  // The "$0 Free" pill: first in the new-session MODEL row (#nsModelPickerPills).
  // Picking it turns the free runtime on (and the engine to Claude); picking
  // any other model pill turns it off. app.js re-renders the row, so sync()
  // (called from the renderer and on engine changes) re-inserts the pill.
  function ensurePill() {
    var box = document.getElementById('nsModelPickerPills');
    if (!box) return null;
    var pill = box.querySelector('.ccc-free-pill');
    if (!pill) {
      pill = document.createElement('button');
      pill.type = 'button';
      pill.className = 'ccc-free-pill';
      pill.setAttribute('role', 'radio');
      pill.innerHTML = '<span class="ccc-free-pill-badge">$0</span><span class="ccc-free-pill-label">Free</span>';
      box.insertBefore(pill, box.firstChild);
    }
    return pill;
  }

  function getCurrentEngine() {
    var sel = document.getElementById('convInputEngineSelect');
    if (sel && sel.value) return sel.value;
    try {
      return (typeof getSpawnEngine === 'function') ? getSpawnEngine() : '';
    } catch (_) { return ''; }
  }

  function setEnabled(on) {
    state.enabled = !!on;
    try { localStorage.setItem(LS_KEY, state.enabled ? '1' : '0'); } catch (_) {}
  }

  function useClaudeEngine() {
    var sel = document.getElementById('convInputEngineSelect');
    if (!sel || sel.value === 'claude') return;
    sel.value = 'claude';
    sel.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function paint(engine) {
    var pill = ensurePill();
    if (!pill) return;
    var s = engineStatus('claude');
    var notReady = !!(state.status && !(s && s.ready));
    var on = state.enabled && supports(engine) && !notReady;
    pill.classList.toggle('is-selected', on);
    pill.classList.toggle('is-setup', notReady);
    pill.setAttribute('aria-checked', on ? 'true' : 'false');
    pill.querySelector('.ccc-free-pill-label').textContent = notReady ? 'Set up free models' : 'Free';
    pill.title = notReady
      ? 'Free models are not set up yet. Click to set them up.'
      : 'Runs on your free router. Costs $0.';
    var box = pill.parentNode;
    if (box) box.classList.toggle('ccc-free-on', on);
    // Composer: on the new-session screen the model/effort menus give way
    // to a "Free model" label (CSS below keys off this body class).
    document.body.classList.toggle('ccc-free-on', on);
    ensureComposerLabel();
  }

  function ensureComposerLabel() {
    if (document.getElementById('cccFreeComposerLabel')) return;
    var eng = document.getElementById('convInputEngineSelect');
    if (!eng || !eng.parentNode) return;
    var el = document.createElement('span');
    el.id = 'cccFreeComposerLabel';
    el.className = 'ccc-free-composer-label';
    el.title = 'Your free router picks the best free model. Costs $0.';
    el.innerHTML = '<span class="ccc-free-pill-badge">$0</span>Free model';
    eng.parentNode.insertBefore(el, eng.nextSibling);
  }

  // Called by syncSpawnEngineDependentUi and the pill renderer.
  function sync(engine) {
    ensureStyles();
    paint(engine || getCurrentEngine());
    refreshStatus(false).then(function () { paint(getCurrentEngine()); });
  }

  document.addEventListener('click', function (ev) {
    var t = ev.target;
    if (!t || !t.closest) return;
    var pill = t.closest('.ccc-free-pill');
    if (pill) {
      ev.preventDefault();
      var turnOn = function () {
        setEnabled(true);
        useClaudeEngine();
        paint('claude');
      };
      if (pill.classList.contains('is-setup')) {
        // A "not ready" can be a stale probe from a busy page load: re-check
        // before sending the user to setup.
        refreshStatus(true).then(function () {
          if (readyFor('claude')) turnOn();
          else window.open('/free-router', '_blank');
        });
        return;
      }
      turnOn();
      return;
    }
    if (t.closest('#nsModelPickerPills .orch-tier-chip')) {
      setEnabled(false);
      paint(getCurrentEngine());
    }
  }, true);

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
    document.addEventListener('DOMContentLoaded', function () { ensureStyles(); });
  } else {
    ensureStyles();
  }
})();
