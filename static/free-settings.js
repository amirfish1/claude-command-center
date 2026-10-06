/* Settings > Free models panel.
 *
 * Injected at boot: one rail tab ("Free models") + one section
 * (data-section-id="free") inside the Settings modal. All DOM, styles and
 * state live here so shared hot files only carry a single <script> tag.
 *
 * Data sources (each optional — a 404 means "that part isn't in this
 * build yet" and the panel degrades, never crashes):
 *   GET  /api/free-router/status      router install/run/health (sibling)
 *   POST /api/free-router/install|start|stop
 *   GET  /api/free-router/providers   rich provider registry (sibling)
 *   POST /api/free-router/keys        {platform,key} -> {ok,validated,error}
 *   GET  /api/free-router/models      ranked free models (sibling)
 *   GET  /api/free-settings/*         our own admin proxy (this lane)
 *
 * Delight hooks are all optional: window.cccFx (sound/confetti/countUp),
 * window.cccOnboarding (guided setup), window.cccNotify (toasts).
 */
(function () {
  'use strict';

  var SECTION_ID = 'free';
  var POLL_MS = 15000;
  var JOB_POLL_MS = 1000;

  // ── tiny helpers ─────────────────────────────────────────────────────

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function el(id) { return document.getElementById(id); }

  function api(path, opts) {
    opts = opts || {};
    var init = { method: opts.method || 'GET', headers: { Accept: 'application/json' } };
    if (opts.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    return fetch(path, init).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        return { status: r.status, data: data };
      });
    }).catch(function () {
      return { status: 0, data: { ok: false, error: 'Could not reach the CCC server.' } };
    });
  }

  function fx(name) {
    try {
      if (window.cccFx && typeof window.cccFx.play === 'function') window.cccFx.play(name);
    } catch (_) { /* sounds are optional */ }
  }

  function confetti(opts) {
    try {
      if (window.cccFx && typeof window.cccFx.confetti === 'function' &&
          !(window.cccFx.reducedMotion && window.cccFx.reducedMotion())) {
        window.cccFx.confetti(opts);
      }
    } catch (_) { /* confetti is optional */ }
  }

  function notify(title, body) {
    try {
      if (typeof window.cccNotify === 'function') window.cccNotify({ title: title, body: body, kind: 'free' });
    } catch (_) { /* notifications are optional */ }
  }

  function money(n) {
    n = Number(n) || 0;
    if (n > 0 && n < 0.01) return '$' + n.toFixed(4);
    return '$' + n.toFixed(2);
  }

  function num(n) {
    n = Number(n) || 0;
    if (n >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, '') + 'B';
    if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'k';
    return String(Math.round(n));
  }

  // ── DOM injection ────────────────────────────────────────────────────

  function injectCss() {
    if (el('freeSettingsCss')) return;
    var link = document.createElement('link');
    link.id = 'freeSettingsCss';
    link.rel = 'stylesheet';
    link.href = '/static/free-settings.css';
    document.head.appendChild(link);
  }

  function buildRailItem() {
    if (el('settingsRailTab-' + SECTION_ID)) return;
    var rail = el('settingsRail');
    if (!rail) return;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'settings-rail-item';
    btn.id = 'settingsRailTab-' + SECTION_ID;
    btn.setAttribute('data-section-target', SECTION_ID);
    btn.setAttribute('role', 'tab');
    btn.setAttribute('aria-selected', 'false');
    btn.setAttribute('aria-controls', 'settingsSection-' + SECTION_ID);
    btn.innerHTML =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13"/>' +
      '<path d="M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/>' +
      '<path d="M7.5 8a2.5 2.5 0 0 1 0-5C11 3 12 8 12 8s1-5 4.5-5a2.5 2.5 0 0 1 0 5"/></svg>' +
      '<span>Free models</span>';
    var after = el('settingsRailTab-engines') || el('settingsRailTab-vault');
    if (after && after.nextSibling) rail.insertBefore(btn, after.nextSibling);
    else rail.appendChild(btn);
  }

  function buildSection() {
    if (el('settingsSection-' + SECTION_ID)) return;
    var pane = el('settingsPane');
    if (!pane) return;
    var sec = document.createElement('section');
    sec.className = 'settings-section';
    sec.id = 'settingsSection-' + SECTION_ID;
    sec.setAttribute('data-section-id', SECTION_ID);
    sec.setAttribute('role', 'tabpanel');
    sec.setAttribute('aria-labelledby', 'settingsRailTab-' + SECTION_ID);
    sec.setAttribute('aria-hidden', 'true');
    sec.innerHTML =
      '<div class="settings-section-eyebrow">Free models</div>' +
      '<div class="settings-section-note">Run your agents on free models for $0 a run. ' +
      'A tiny router on this Mac sends work to providers that cost nothing.</div>' +

      '<div class="settings-row" data-keywords="free models router engine zero cost $0 savings install start stop status">' +
        '<div class="settings-row-main">' +
          '<div class="settings-row-label">Free engine</div>' +
          '<div class="settings-row-desc" id="fsHeroDesc">Checking the free engine\u2026</div>' +
        '</div>' +
      '</div>' +
      '<div id="fsRoot">' +
        '<div class="fs-skel" aria-hidden="true"><i></i><i></i><i></i></div>' +
      '</div>';
    var after = el('settingsSection-engines');
    if (after && after.nextSibling) pane.insertBefore(sec, after.nextSibling);
    else pane.appendChild(sec);

    // Refresh whenever the section becomes the active pane (rail click,
    // deep link, search result, programmatic switch — all change the class).
    new MutationObserver(function () {
      var active = sec.classList.contains('is-active-section');
      if (active) activate(); else deactivate();
    }).observe(sec, { attributes: true, attributeFilter: ['class'] });
  }

  // Deep link (?ccc_settings=free) — the modal's own lookup ran before our
  // deferred script injected the tab, so we re-do it for our id only.
  function maybeDeepLink() {
    try {
      if (new URLSearchParams(location.search).get('ccc_settings') !== SECTION_ID) return;
      setTimeout(function () {
        var s = el('settingsBtn');
        var t = el('settingsRailTab-' + SECTION_ID);
        if (s) s.click();
        if (t) t.click();
      }, 30);
    } catch (_) { /* older browsers: no deep link */ }
  }

  // ── state + refresh ─────────────────────────────────────────────────

  var S = {
    loaded: false, loading: false,
    ping: null, status: null,
    providers: null, routerProviders: null,
    keys: null, usage: null, usageRange: '7d',
    strategy: null, strategyMsg: null, models: null,
    job: null, jobError: null, jobTicks: 0,
    showAllProviders: false,
    keyForms: {},          // platform -> {busy, error, ok}
    confirmRemove: null,   // key id pending inline confirm
    pollTimer: null, jobTimer: null, active: false,
  };

  // Fallback hint copy for the sibling status lane's richer states; when
  // window.cccFreeRouter.describe is present its wording wins.
  var STATE_HINTS = {
    needs_key: 'Add one free provider below and your agents can run for $0.',
    degraded: 'All free providers are cooling down. Try again soon.',
    needs_setup: 'The unified free key was not minted yet. Try reinstalling.',
  };

  function activate() {
    if (S.active) { refresh(); return; }
    S.active = true;
    refresh();
    S.pollTimer = setInterval(function () {
      if (!document.hidden) refresh(true);
    }, POLL_MS);
  }

  function deactivate() {
    S.active = false;
    if (S.pollTimer) { clearInterval(S.pollTimer); S.pollTimer = null; }
    if (S.jobTimer) { clearTimeout(S.jobTimer); S.jobTimer = null; }
  }

  function refresh(quiet) {
    if (S.loading) { if (!quiet) render(); return; }
    S.loading = true;
    if (!quiet && S.loaded) { /* keep last render; just refetch */ }
    // Two phases: the router status probe is slow (it pings the engine and
    // lists keys upstream), so the cheap calls paint first and the hero
    // fills in when status lands.
    api('/api/free-router/status').then(function (r) {
      S.status = r.status === 200 && r.data && r.data.installed !== undefined ? r.data : null;
      S.loading = false;
      render();
    });
    Promise.all([
      api('/api/free-settings/ping'),
      api('/api/free-router/providers'),
      api('/api/free-settings/providers'),
      api('/api/free-settings/keys'),
      api('/api/free-settings/usage?range=' + S.usageRange),
      api('/api/free-settings/strategy'),
      api('/api/free-router/models'),
    ]).then(function (r) {
      S.ping = r[0].status === 200 && r[0].data && r[0].data.configured !== undefined
        ? r[0].data : { configured: false };
      S.providers = r[1].status === 200 && Array.isArray(r[1].data) ? r[1].data : null;
      S.routerProviders = r[2].status === 200 && r[2].data.ok ? r[2].data.providers : null;
      S.keys = r[3].status === 200 && r[3].data.ok ? r[3].data.keys : null;
      S.keysError = r[3].status === 200 && !r[3].data.ok ? r[3].data : (r[3].status !== 200 ? { code: 'upstream_error' } : null);
      S.usage = r[4].status === 200 && r[4].data.ok ? r[4].data : null;
      S.strategy = r[5].status === 200 && r[5].data.ok ? r[5].data.strategy : null;
      S.models = r[6].status === 200 && Array.isArray(r[6].data) ? r[6].data : null;
      S.loaded = true;
      render();
    });
  }

  // derived view of the router, merging the sibling status endpoint with
  // what our own admin calls observed (works pre-merge of the status lane)
  function routerState() {
    var reachable = !!(S.keys !== null || (S.usage && S.usage.ok !== false));
    if (S.status) {
      return {
        installed: !!S.status.installed,
        running: !!S.status.running,
        healthy: !!S.status.healthy,
        state: S.status.state || null,
        port: S.status.port, version: S.status.version,
        nodeOk: S.status.node_ok !== false,
        baseUrl: S.status.base_url,
      };
    }
    var configured = !!(S.ping && S.ping.configured);
    return {
      installed: configured || reachable,
      running: reachable,
      healthy: reachable,
      state: null,
      port: S.ping && S.ping.base_url ? Number(String(S.ping.base_url).split(':').pop()) : null,
      version: null, nodeOk: true,
      baseUrl: S.ping && S.ping.base_url,
    };
  }

  // ── render ──────────────────────────────────────────────────────────

  function render() {
    var root = el('fsRoot');
    var desc = el('fsHeroDesc');
    if (!root || !desc) return;
    if (!S.loaded) return;

    var st = routerState();
    var html = '';
    html += renderHero(st);
    if (st.installed) html += renderStrategy(st);
    html += renderProviders(st);
    if (st.running) html += renderUsage();
    html += renderLeaderboard();
    html += renderFooter();
    root.innerHTML = html;
    wire(root);

    var heroDesc = st.healthy && st.running
      ? 'Running. Every task on this engine costs $0.'
      : st.installed
        ? (st.running ? 'Starting up\u2026' : 'Installed, not running.')
        : 'Not set up yet.';
    desc.textContent = heroDesc;
  }

  function renderHero(st) {
    var inner = '';
    if (S.job) {
      var j = S.job;
      var pct = Math.round((j.progress || 0) * 100);
      var lastLine = (j.lines && j.lines.length) ? j.lines[j.lines.length - 1] : '';
      inner =
        '<div class="fs-hero-badge">$0</div>' +
        '<div class="fs-hero-main">' +
          '<div class="fs-hero-title">Setting up your free engine\u2026</div>' +
          '<div class="fs-progress"><i style="width:' + pct + '%"></i></div>' +
          '<div class="fs-hero-sub fs-mono">' + esc(lastLine || 'Working\u2026') + '</div>' +
        '</div>';
    } else if (!st.installed) {
      inner =
        '<div class="fs-hero-badge">$0</div>' +
        '<div class="fs-hero-main">' +
          '<div class="fs-hero-title">Meet your free engine</div>' +
          '<div class="fs-hero-sub">One click installs a small local router that sends your agents to free models. ' +
          'Same sessions, zero cost.</div>' +
          (S.jobError ? '<div class="fs-note is-error">' + esc(S.jobError) + '</div>' : '') +
        '</div>' +
        '<div class="fs-hero-actions">' +
          '<button type="button" class="fs-btn fs-btn-primary" id="fsInstallBtn">Set up free models</button>' +
        '</div>';
    } else if (!st.running) {
      inner =
        '<div class="fs-hero-badge fs-off">$0</div>' +
        '<div class="fs-hero-main">' +
          '<div class="fs-hero-title">Your free engine is off</div>' +
          '<div class="fs-hero-sub">It is installed on this Mac. Start it to run agents for $0.</div>' +
          (S.jobError ? '<div class="fs-note is-error">' + esc(S.jobError) + '</div>' : '') +
        '</div>' +
        '<div class="fs-hero-actions">' +
          '<button type="button" class="fs-btn fs-btn-primary" id="fsStartBtn">Start free engine</button>' +
        '</div>';
    } else {
      var sub = st.healthy
        ? 'On port ' + esc(st.port || '?') + (st.version ? ' \u00b7 v' + esc(st.version) : '') +
          '. Every run on it costs $0.'
        : 'Started. Warming up the model list\u2026';
      // Richer states from the sibling status lane (needs_key, degraded,
      // needs_setup) surface as a warm hint under the hero copy.
      var stateHint = '';
      if (st.healthy && st.state && STATE_HINTS[st.state]) {
        var d = window.cccFreeRouter && window.cccFreeRouter.describe
          ? window.cccFreeRouter.describe(S.status) : null;
        stateHint = '<div class="fs-note is-warn">' +
          esc((d && d.tone === 'warn' && d.detail) || STATE_HINTS[st.state]) + '</div>';
      }
      inner =
        '<div class="fs-hero-badge">$0</div>' +
        '<div class="fs-hero-main">' +
          '<div class="fs-hero-title">Free engine is ' + (st.healthy ? 'running' : 'starting') + '</div>' +
          '<div class="fs-hero-sub">' + sub + '</div>' +
          stateHint +
          (S.jobError ? '<div class="fs-note is-error">' + esc(S.jobError) + '</div>' : '') +
        '</div>' +
        '<div class="fs-hero-actions">' +
          '<span class="fs-pill ' + (st.healthy ? 'is-ok' : 'is-warm') + '">' + (st.healthy ? 'On' : 'Warming up') + '</span>' +
          '<button type="button" class="fs-btn" id="fsStopBtn">Stop</button>' +
        '</div>';
    }
    return '<div class="fs-hero">' + inner + '</div>';
  }

  function renderStrategy(st) {
    if (!st.running) return '';
    var map = (S.strategy && S.strategy.map) || {};
    var current = map['default'] || 'auto';
    var opts = ['<option value="auto"' + (current === 'auto' ? ' selected' : '') +
      '>Best pick (auto)</option>'];
    (S.models || []).forEach(function (m) {
      if (!m || m.ready === false) return;
      var id = String(m.id || '');
      if (!id || id === 'auto') return;
      opts.push('<option value="' + esc(id) + '"' + (current === id ? ' selected' : '') + '>' +
        esc(m.name || id) + (m.platform ? ' \u00b7 ' + esc(m.platform) : '') + '</option>');
    });
    if (current !== 'auto' && !(S.models || []).some(function (m) { return String(m.id) === current; })) {
      opts.push('<option value="' + esc(current) + '" selected>' + esc(current) + '</option>');
    }
    return '' +
      '<div class="settings-row" data-keywords="free model choice routing strategy auto best pick default model">' +
        '<div class="settings-row-main">' +
          '<div class="settings-row-label">Model choice</div>' +
          '<div class="settings-row-desc">Which free model your agents use. Auto keeps picking the best one for you.</div>' +
        '</div>' +
        '<div class="settings-row-control">' +
          '<select class="settings-select" id="fsModelSelect" aria-label="Free model choice">' +
          opts.join('') + '</select>' +
        '</div>' +
      '</div>' +
      '<div class="settings-row-note' + (S.strategyMsg ? ' is-' + S.strategyMsg.kind : '') + '" id="fsStrategyNote"' +
      (S.strategyMsg ? '' : ' hidden') + '>' + (S.strategyMsg ? esc(S.strategyMsg.text) : '') + '</div>';
  }

  function providerMeta() {
    // Merge the sibling registry (rich metadata) over the router's own list.
    var bySlug = {};
    ((S.routerProviders && S.routerProviders.providers) || S.routerProviders || [])
      .forEach(function (p) { if (p && p.platform) bySlug[p.platform] = { platform: p.platform, name: p.name, keyless: !!p.keyless }; });
    (S.providers || []).forEach(function (p) {
      if (!p || !p.platform) return;
      bySlug[p.platform] = Object.assign(bySlug[p.platform] || {}, p);
    });
    return bySlug;
  }

  function renderProviders(st) {
    var meta = providerMeta();
    var keys = S.keys || [];
    var byPlatform = {};
    keys.forEach(function (k) {
      var p = String(k.platform || '');
      (byPlatform[p] = byPlatform[p] || []).push(k);
    });

    var usage = {};
    (S.usage && S.usage.by_platform || []).forEach(function (u) {
      if (u && u.platform) usage[u.platform] = u;
    });

    // Ordering: providers with keys first, then keyless offers, then by
    // coding score (or name when the registry lane is not merged yet).
    var slugs = Object.keys(meta);
    Object.keys(byPlatform).forEach(function (p) { if (!meta[p]) meta[p] = { platform: p, name: p }; });
    slugs = Object.keys(meta);
    function scoreOf(p) {
      var m = meta[p] || {};
      var s = 0;
      if (byPlatform[p] && byPlatform[p].length) s += 1000;
      if (m.keyless) s += 100;
      s += Number(m.coding_score) || 0;
      return s;
    }
    slugs.sort(function (a, b) {
      var d = scoreOf(b) - scoreOf(a);
      return d || String(meta[a].name || a).localeCompare(String(meta[b].name || b));
    });
    var visible = S.showAllProviders ? slugs : slugs.slice(0, 8);
    var hiddenCount = slugs.length - visible.length;

    var cards = visible.map(function (slug) {
      return providerCard(slug, meta[slug] || {}, byPlatform[slug] || [], usage[slug], st);
    }).join('');

    var errNote = '';
    if (!st.running && st.installed) {
      errNote = '<div class="fs-note">Start the free engine above to manage providers.</div>';
    } else if (!st.installed) {
      errNote = '<div class="fs-note">Set up the free engine above, then add a provider here.</div>';
    } else if (S.keysError && S.keysError.code === 'router_unreachable') {
      errNote = '<div class="fs-note is-error">The router is not answering. Try stopping and starting it.</div>';
    } else if (S.keysError) {
      errNote = '<div class="fs-note is-error">' + esc(S.keysError.error || 'Providers are unavailable right now.') + '</div>';
    }

    return '' +
      '<div class="settings-subsection-label" style="margin-top:18px">Providers</div>' +
      '<div class="settings-row" data-keywords="free providers keys api key signup add remove enable disable">' +
        '<div class="settings-row-main">' +
          '<div class="settings-row-desc">Free LLM providers. Turn on one to start; add more for backup.</div>' +
        '</div>' +
      '</div>' +
      errNote +
      '<div class="fs-providers" id="fsProviders">' + (cards ||
        (st.running && !S.keysError ? '<div class="fs-empty">Loading providers\u2026</div>' : '')) +
      '</div>' +
      (hiddenCount > 0 ?
        '<button type="button" class="fs-link" id="fsMoreProviders">Show ' + hiddenCount + ' more providers</button>' :
        (S.showAllProviders && slugs.length > 8 ?
          '<button type="button" class="fs-link" id="fsMoreProviders">Show fewer</button>' : ''));
  }

  function providerCard(slug, m, keys, usageRow, st) {
    var name = m.name || slug;
    var badges = '';
    if (m.keyless) badges += '<span class="fs-tag">No signup</span>';
    if (m.free_no_card) badges += '<span class="fs-tag">Free, no card</span>';
    if (m.free_no_card === false && !m.keyless) badges += '';
    var tos = m.tos_note || (m.keyless ?
      'Free with no account. This provider logs prompts to improve their models.' : '');
    // Consent-gated providers (kilo logs prompts for training) get the warn
    // tint so the trade-off is impossible to miss before the click.
    var tosCls = 'fs-tos' + (m.keyless && m.needs_consent ? ' is-warn' : '');

    var body = '';
    if (keys.length) {
      body += keys.map(function (k) { return keyRow(k, st); }).join('');
      // Consent disclosure stays visible after enabling — the prompt-logging
      // trade-off is a standing fact, not a one-time gate.
      if (m.needs_consent && tos) body += '<div class="fs-tos">' + esc(tos) + '</div>';
      var enabledAny = keys.some(function (k) { return !!k.enabled; });
      if (keys.length > 1 || m.keyless) {
        body += '<div class="fs-key-actions">' +
          '<button type="button" class="fs-link" data-fs-toggle-platform="' + esc(slug) + '" data-fs-on="' +
          (enabledAny ? '0' : '1') + '">' + (enabledAny ? 'Pause provider' : 'Resume provider') + '</button>' +
        '</div>';
      }
      // A keyed provider can still take another key (backup credential).
      if (!m.keyless) {
        body += addKeyForm(slug, m, 'Add another key');
      }
    } else if (m.keyless) {
      var kf = S.keyForms[slug] || {};
      body += '<div class="' + tosCls + '">' + esc(tos) + '</div>' +
        '<div class="fs-key-actions">' +
          '<button type="button" class="fs-btn fs-btn-primary fs-btn-sm" data-fs-enable-keyless="' + esc(slug) + '"' +
          (st.running ? '' : ' disabled') + '>Turn on ' + esc(name) + '</button>' +
        '</div>' +
        (kf.error ? '<div class="fs-note is-error">' + esc(kf.error) + '</div>' : '') +
        (!kf.error && kf.ok ? '<div class="fs-note is-ok">' + esc(kf.ok) + '</div>' : '');
    } else {
      body += (tos ? '<div class="' + tosCls + '">' + esc(tos) + '</div>' : '') + addKeyForm(slug, m, 'Add key');
    }

    var usageChip = '';
    if (usageRow && usageRow.requests) {
      usageChip = '<span class="fs-usage-chip" title="' + esc(usageRow.requests) + ' requests, ' +
        esc(usageRow.successRate) + '% ok">' + num(usageRow.requests) + ' runs</span>';
    }
    var signup = (!m.keyless && m.signup_url) ?
      '<a class="fs-link" href="' + esc(m.signup_url) + '" target="_blank" rel="noopener">Get a free key \u2197</a>' : '';

    return '<div class="fs-provider' + (keys.some(function (k) { return k.enabled; }) ? ' is-live' : '') + '">' +
      '<div class="fs-provider-head">' +
        '<div class="fs-provider-name">' + esc(name) + '</div>' +
        '<div class="fs-provider-badges">' + badges + usageChip + '</div>' +
      '</div>' +
      '<div class="fs-provider-sub">' + esc(m.tagline ||
        (m.key_hint ? 'Key looks like ' + m.key_hint
          : (m.keyless ? 'Free, no signup needed' : (m.platform || slug)))) + '</div>' +
      body + signup +
    '</div>';
  }

  function keyRow(k, st) {
    var status = String(k.status || 'unknown');
    var dot = (status === 'valid' || status === 'ok' || status === 'healthy') ? 'ok'
      : (status === 'error' || status === 'invalid' ? 'bad' : 'idle');
    var cooling = (k.cooldowns || []).length > 0;
    if (cooling) dot = 'warm';
    var title = cooling ? 'Cooling down after errors, back soon'
      : (status === 'valid' || status === 'ok' || status === 'healthy') ? 'Working'
      : status === 'error' || status === 'invalid' ? (k.lastHealthError || 'This key was rejected')
      : 'Not checked yet';
    var usage = k.monthlyUsage && k.monthlyUsage.requests ?
      '<span class="fs-key-usage">' + num(k.monthlyUsage.requests) + ' this month</span>' : '';
    var removeOpen = (S.confirmRemove === k.id);
    return '<div class="fs-key">' +
      '<span class="fs-dot is-' + dot + '" title="' + esc(title) + '"></span>' +
      '<code class="fs-key-mask">' + esc(k.maskedKey || '\u2022\u2022\u2022\u2022') + '</code>' +
      (k.label ? '<span class="fs-key-label">' + esc(k.label) + '</span>' : '') +
      usage +
      '<span class="fs-key-spacer"></span>' +
      (removeOpen ?
        '<span class="fs-confirm">Remove?' +
          '<button type="button" class="fs-link fs-danger" data-fs-remove="' + k.id + '" data-fs-yes="1">Yes</button>' +
          '<button type="button" class="fs-link" data-fs-remove="' + k.id + '" data-fs-yes="0">No</button>' +
        '</span>' :
        '<button type="button" class="settings-toggle' + (k.enabled ? ' is-on' : '') + '" role="switch" ' +
          'aria-checked="' + (k.enabled ? 'true' : 'false') + '" aria-label="Enable key" ' +
          'data-fs-toggle="' + k.id + '" data-fs-on="' + (k.enabled ? '0' : '1') + '">' +
          '<span class="settings-toggle-track"><span class="settings-toggle-thumb"></span></span></button>' +
        '<button type="button" class="fs-icon-btn" title="Remove key" aria-label="Remove key" ' +
          'data-fs-remove="' + k.id + '">\u00d7</button>') +
    '</div>';
  }

  function addKeyForm(slug, m, label) {
    var f = S.keyForms[slug] || {};
    var hint = m.key_hint ? ' (' + m.key_hint + ')' : '';
    return '<div class="fs-addkey" data-fs-form="' + esc(slug) + '">' +
      '<input type="password" class="settings-text-input" placeholder="Paste a free API key' + esc(hint) + '" ' +
        'autocomplete="off" spellcheck="false" aria-label="' + esc((m.name || slug) + ' API key') + '">' +
      '<button type="button" class="fs-btn fs-btn-sm" data-fs-add="' + esc(slug) + '"' +
        (f.busy ? ' disabled' : '') + '>' + (f.busy ? 'Checking\u2026' : label) + '</button>' +
      (f.error ? '<div class="fs-note is-error">' + esc(f.error) + '</div>' : '') +
      (f.ok ? '<div class="fs-note is-ok">' + esc(f.ok) + '</div>' : '') +
    '</div>';
  }

  function renderUsage() {
    var u = S.usage;
    if (!u) return '';
    var s = u.summary || {};
    var saved = Number(s.estimatedCostSavings) || 0;
    var rows = (u.by_platform || []).slice(0, 8);
    var ranges = [['24h', '24h'], ['7d', '7 days'], ['30d', '30 days']];
    return '' +
      '<div class="settings-subsection-label" style="margin-top:18px">What your free engine has done</div>' +
      '<div class="settings-row" data-keywords="free usage savings requests tokens providers stats">' +
        '<div class="settings-row-main">' +
          '<div class="settings-row-label">Free runs</div>' +
          '<div class="settings-row-desc">Work the router did, priced at normal API rates.</div>' +
        '</div>' +
        '<div class="settings-row-control">' +
          '<div class="settings-segmented" role="group" aria-label="Usage range">' +
          ranges.map(function (r) {
            return '<button type="button" class="settings-segment' + (S.usageRange === r[0] ? ' is-active' : '') +
              '" data-fs-range="' + r[0] + '">' + r[1] + '</button>';
          }).join('') +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div class="fs-usage">' +
        '<div class="fs-usage-hero">' +
          '<div class="fs-saved">' + money(saved) + '</div>' +
          '<div class="fs-saved-sub">of API-priced work for <b>$0</b></div>' +
        '</div>' +
        '<div class="fs-usage-chips">' +
          '<span class="fs-chip">' + num(s.totalRequests) + ' runs</span>' +
          '<span class="fs-chip">' + num((s.totalInputTokens || 0) + (s.totalOutputTokens || 0)) + ' tokens</span>' +
          '<span class="fs-chip">' + (s.successRate != null ? esc(s.successRate) : '\u2014') + '% ok</span>' +
        '</div>' +
        (rows.length ?
          '<div class="fs-utable">' + rows.map(function (r) {
            return '<div class="fs-utrow">' +
              '<span class="fs-utname">' + esc(r.endpoint || r.platform) + '</span>' +
              '<span>' + num(r.requests) + ' runs</span>' +
              '<span>' + num((r.totalInputTokens || 0) + (r.totalOutputTokens || 0)) + ' tok</span>' +
              '<span>' + (r.successRate != null ? esc(r.successRate) + '% ok' : '') + '</span>' +
            '</div>';
          }).join('') + '</div>' :
          '<div class="fs-empty">No runs yet. Start a session on a free model and watch the savings pile up.</div>') +
      '</div>';
  }

  function renderLeaderboard() {
    var models = (S.models || []).filter(function (m) { return m && m.ready !== false; });
    if (!models.length) return '';
    models = models.slice().sort(function (a, b) {
      var ra = a.rank != null ? a.rank : 1e9, rb = b.rank != null ? b.rank : 1e9;
      if (ra !== rb) return ra - rb;
      return (Number(b.score) || 0) - (Number(a.score) || 0);
    }).slice(0, 5);
    return '' +
      '<div class="settings-subsection-label" style="margin-top:18px">Free model leaderboard</div>' +
      '<div class="settings-row" data-keywords="free model leaderboard ranking best score">' +
        '<div class="settings-row-main">' +
          '<div class="settings-row-desc">Top free models on this machine right now, ranked by real coding tasks.</div>' +
        '</div>' +
      '</div>' +
      '<div class="fs-board">' + models.map(function (m, i) {
        return '<div class="fs-brow">' +
          '<span class="fs-brank">' + (i + 1) + '</span>' +
          '<span class="fs-bname">' + esc(m.name || m.id) + '</span>' +
          '<span class="fs-bplat">' + esc(m.platform || '') + '</span>' +
          (m.score != null ? '<span class="fs-bscore">' + esc(m.score) + '</span>' : '') +
        '</div>';
      }).join('') + '</div>';
  }

  function renderFooter() {
    return '<div class="fs-foot">' +
      'Keys are stored by the router on this Mac and are never shown again. ' +
      'Nothing leaves this machine except calls to the providers you turn on.' +
    '</div>';
  }

  // ── actions ─────────────────────────────────────────────────────────

  function wire(root) {
    var b;
    if ((b = el('fsInstallBtn'))) b.onclick = doInstall;
    if ((b = el('fsStartBtn'))) b.onclick = doStart;
    if ((b = el('fsStopBtn'))) b.onclick = doStop;
    if ((b = el('fsMoreProviders'))) b.onclick = function () {
      S.showAllProviders = !S.showAllProviders; render();
    };
    if ((b = el('fsModelSelect'))) b.onchange = function () { doStrategy(b.value); };
    root.querySelectorAll('[data-fs-range]').forEach(function (seg) {
      seg.onclick = function () {
        S.usageRange = seg.getAttribute('data-fs-range');
        S.usage = null;
        api('/api/free-settings/usage?range=' + S.usageRange).then(function (r) {
          if (r.status === 200 && r.data.ok) S.usage = r.data;
          render();
        });
      };
    });
    root.querySelectorAll('[data-fs-toggle]').forEach(function (t) {
      t.onclick = function () {
        doKeyEnable(Number(t.getAttribute('data-fs-toggle')),
                    t.getAttribute('data-fs-on') === '1');
      };
    });
    root.querySelectorAll('[data-fs-remove]').forEach(function (t) {
      t.onclick = function () {
        var id = Number(t.getAttribute('data-fs-remove'));
        if (t.getAttribute('data-fs-yes') === '1') doKeyRemove(id);
        else { S.confirmRemove = t.getAttribute('data-fs-yes') === '0' ? null : id; render(); }
      };
    });
    root.querySelectorAll('[data-fs-toggle-platform]').forEach(function (t) {
      t.onclick = function () {
        doPlatformEnable(t.getAttribute('data-fs-toggle-platform'),
                         t.getAttribute('data-fs-on') === '1');
      };
    });
    root.querySelectorAll('[data-fs-enable-keyless]').forEach(function (t) {
      t.onclick = function () { doEnableKeyless(t.getAttribute('data-fs-enable-keyless'), t); };
    });
    root.querySelectorAll('[data-fs-add]').forEach(function (t) {
      t.onclick = function () {
        var slug = t.getAttribute('data-fs-add');
        var wrap = root.querySelector('[data-fs-form="' + slug + '"]');
        var input = wrap ? wrap.querySelector('input') : null;
        doAddKey(slug, input ? input.value : '');
      };
    });
    root.querySelectorAll('.fs-addkey input').forEach(function (inp) {
      inp.onkeydown = function (e) {
        if (e.key !== 'Enter') return;
        var wrap = inp.closest('[data-fs-form]');
        var btn = wrap ? wrap.querySelector('[data-fs-add]') : null;
        if (btn) btn.click();
      };
    });
  }

  function busy(elBtn, on) {
    if (elBtn) elBtn.disabled = !!on;
  }

  function doInstall() {
    S.jobError = null;
    S.jobTicks = 0;
    S.job = { status: 'running', progress: 0, lines: ['Getting things ready\u2026'] };
    render();
    api('/api/free-router/install', { method: 'POST', body: {} }).then(function (r) {
      var jid = r.data && (r.data.job_id || (r.data.job && r.data.job.id));
      if (r.status === 404 || !jid) {
        S.job = null;
        if (window.cccOnboarding && typeof window.cccOnboarding.open === 'function') {
          window.cccOnboarding.open();
          return;
        }
        S.jobError = 'The installer is not in this build yet. Run the welcome guide from Help & Onboarding.';
        render();
        return;
      }
      pollJob(jid);
    });
  }

  function pollJob(jid) {
    api('/api/free-router/jobs/' + encodeURIComponent(jid)).then(function (r) {
      var j = r.data || {};
      S.jobTicks++;
      if (r.status !== 200) {
        S.job = null; S.jobError = 'Setup status is unavailable. Try again in a moment.';
        render(); return;
      }
      if (j.status === 'done') {
        S.job = null;
        fx('success'); confetti({ particleCount: 90 });
        notify('Free engine ready', 'Setup finished. Runs on it cost $0.');
        refresh(); return;
      }
      if (j.status === 'error') {
        S.job = null; S.jobError = j.error || 'Setup hit a snag. Try again.';
        fx('error'); render(); return;
      }
      if (S.jobTicks > 900) {
        S.job = null; S.jobError = 'Setup is taking unusually long. Try again.';
        fx('error'); render(); return;
      }
      S.job = j;
      render();
      S.jobTimer = setTimeout(function () { pollJob(jid); }, JOB_POLL_MS);
    });
  }

  function doStart(ev) {
    busy(ev && ev.target, true);
    api('/api/free-router/start', { method: 'POST', body: {} }).then(function (r) {
      if (r.status === 404) {
        S.jobError = 'Start is not wired in this build yet.';
      } else if (!r.data || r.data.ok === false) {
        S.jobError = (r.data && r.data.error) || 'The engine would not start. Try again.';
        fx('error');
      } else {
        S.jobError = null;
        fx('step');
      }
      refresh();
    });
  }

  function doStop(ev) {
    busy(ev && ev.target, true);
    api('/api/free-router/stop', { method: 'POST', body: {} }).then(function (r) {
      if (r.status === 404) {
        S.jobError = 'Stop is not wired in this build yet.';
      } else if (r.data && r.data.ok === false) {
        S.jobError = (r.data && r.data.error) || 'Could not stop the engine.';
        fx('error');
      } else {
        S.jobError = null;
      }
      refresh();
    });
  }

  function doStrategy(model) {
    api('/api/free-settings/strategy', { method: 'POST', body: { family: 'default', model: model } })
      .then(function (r) {
        if (r.status === 200 && r.data.ok) {
          S.strategyMsg = {
            kind: 'ok',
            text: model === 'auto'
              ? 'Auto pick is on. CCC will use the best free model.'
              : 'Pinned. Free runs now use ' + model + '.',
          };
          fx('success');
        } else {
          S.strategyMsg = {
            kind: 'error',
            text: (r.data && r.data.error) || 'Could not save that choice.',
          };
          fx('error');
        }
        refresh();
      });
  }

  function doKeyEnable(id, on) {
    api('/api/free-settings/keys/enable', { method: 'POST', body: { id: id, enabled: on } })
      .then(function (r) {
        if (!(r.status === 200 && r.data.ok)) fx('error');
        refresh();
      });
  }

  function doKeyRemove(id) {
    S.confirmRemove = null;
    api('/api/free-settings/keys/remove', { method: 'POST', body: { id: id } })
      .then(function (r) {
        if (r.status === 200 && r.data.ok) fx('step'); else fx('error');
        refresh();
      });
  }

  function doPlatformEnable(slug, on) {
    api('/api/free-settings/platforms/enable', { method: 'POST', body: { platform: slug, enabled: on } })
      .then(function () { refresh(); });
  }

  // Sibling key-submit contract: ok=false means nothing was saved (real
  // error); ok=true + validated=false means "saved, still being checked" —
  // the router's caveat shows as a soft note, not a red error.
  function keySubmitNote(r, okText) {
    var d = r.data || {};
    var errText = (typeof d.error === 'string' && d.error) ||
      (d.error && d.error.message) || null;
    if (r.status >= 400 || d.ok === false) {
      return { kind: 'error', text: errText || 'That did not work — try again.' };
    }
    if (d.validated === false && errText) return { kind: 'ok', text: errText };
    return { kind: 'ok', text: okText };
  }

  function doEnableKeyless(slug, btn) {
    busy(btn, true);
    var meta = providerMeta()[slug] || {};
    var f = S.keyForms[slug] = S.keyForms[slug] || {};
    f.error = null; f.ok = null;
    // Sibling key endpoint first (validates through the router), our own
    // proxy as fallback so the button works before that lane merges. The
    // click on "Turn on" is the ToS consent the endpoint asks for.
    api('/api/free-router/keys', { method: 'POST',
      body: { platform: slug, consent: true } }).then(function (r) {
      if (r.status === 404) {
        return api('/api/free-settings/keys/add', { method: 'POST', body: { platform: slug } });
      }
      return r;
    }).then(function (r) {
      var v = keySubmitNote(r, (meta.name || slug) + ' is on.');
      if (v.kind === 'ok') {
        f.ok = v.text;
        fx('coin');
        notify((meta.name || slug) + ' is on', 'Free runs can now use ' + (meta.name || slug) + '.');
      } else {
        f.error = v.text;
        fx('error');
      }
      refresh();
    });
  }

  function doAddKey(slug, key) {
    key = String(key || '').trim();
    var f = S.keyForms[slug] = S.keyForms[slug] || {};
    var meta = providerMeta()[slug] || {};
    f.error = null; f.ok = null;
    if (!key) { f.error = 'Paste the key first.'; render(); return; }
    // Cheap format check against the registry's hint pattern — saves a
    // round trip and explains what the key should look like.
    if (meta.key_regex) {
      try {
        if (!new RegExp(meta.key_regex).test(key)) {
          f.error = 'That does not look like a ' + (meta.name || slug) + ' key' +
            (meta.key_hint ? ' — they usually look like ' + meta.key_hint : '') + '.';
          render(); return;
        }
      } catch (_) { /* pattern not JS-safe — let the router judge */ }
    }
    f.busy = true; render();
    api('/api/free-router/keys', { method: 'POST',
      body: { platform: slug, key: key, consent: true } }).then(function (r) {
      if (r.status === 404) {
        return api('/api/free-settings/keys/add', { method: 'POST', body: { platform: slug, key: key } });
      }
      return r;
    }).then(function (r) {
      f.busy = false;
      var v = keySubmitNote(r, 'Key saved.');
      if (v.kind === 'ok') { f.ok = v.text; fx('coin'); }
      else { f.error = v.text; fx('error'); }
      render();
      if (v.kind === 'ok') refresh();
    });
  }

  // ── boot ────────────────────────────────────────────────────────────

  injectCss();
  buildRailItem();
  buildSection();
  maybeDeepLink();

  // Rebuild if a hot reload replaced the settings modal wholesale.
  new MutationObserver(function () {
    if (!el('settingsRailTab-' + SECTION_ID)) buildRailItem();
    if (!el('settingsSection-' + SECTION_ID)) buildSection();
  }).observe(document.body, { childList: true, subtree: false });

  window.cccFreeSettings = { refresh: refresh, section: SECTION_ID };
})();
