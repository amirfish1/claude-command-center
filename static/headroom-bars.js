/* Headroom bars: a compact strip of per-vendor plan gauges in the sidebar
 * header — how much of each plan is left and when it refills. Backed by
 * GET /api/headroom (the Leftover Mode engine). A 404 means that engine is
 * not merged/installed: the strip hides and stays quiet forever.
 *
 * Self-contained by design: one fetch per poll, no per-session work. The
 * pure helpers (normalize, risk, formatters) are exported as
 * window.cccHeadroom / module.exports so tests can drive them without a DOM.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  } else {
    root.cccHeadroom = api;
    if (typeof document !== 'undefined') api.boot();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var POLL_MS = 60000;
  var TICK_MS = 30000;
  var ERROR_BACKOFF_MS = 90000;

  // The headroom engine owns the account contract. Unknown readings stay
  // unknown, rather than inferring percentages from another field or
  // pretending that a free provider has an unlimited quota.
  var VENDOR_ORDER = { claude: 0, codex: 1, kimi: 2, devin: 3, free_router: 4 };
  var VENDOR_LABELS = { claude: 'Claude', codex: 'Codex', kimi: 'Kimi', devin: 'Devin', free_router: 'Free models' };

  /* ---------------- pure helpers (also the test surface) ---------------- */

  function num(v) {
    return typeof v === 'number' && isFinite(v) ? v : null;
  }

  function str(v) {
    return typeof v === 'string' && v.trim() !== '' ? v.trim() : null;
  }

  function epochMs(v) {
    var n = num(v);
    // The contract reports epoch seconds, never milliseconds or ISO strings.
    return n != null && n >= 0 && n <= 8640000000000 ? n * 1000 : null;
  }

  function itemsFromPayload(payload) {
    if (!payload || payload.ok !== true || !Array.isArray(payload.rows)) return [];
    // Account rows are already aggregated by the backend.
    return payload.rows;
  }

  function normalizeItem(raw, nowMs) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
    // Each top-level account row describes one quota. Unavailable accounts
    // stay hidden; unknown numbers and free-router limits must not become
    // a fabricated full or empty quota reading.
    var item = raw;
    var engine = str(item.engine);
    var id = str(item.id);
    if (!id || !Object.prototype.hasOwnProperty.call(VENDOR_ORDER, engine) || item.available !== true) return null;
    var unlimited = item.unlimited === true;
    var pctLeft = unlimited ? null : num(item.percent_left);
    if (pctLeft != null && (pctLeft < 0 || pctLeft > 100)) pctLeft = null;
    var projectedPct = num(item.projected_expiring_pct);
    if (projectedPct != null && (projectedPct < 0 || projectedPct > 100)) projectedPct = null;
    var now = nowMs == null ? Date.now() : nowMs;
    var resetAtMs = epochMs(item.resets_at);
    var hours = num(item.hours_to_reset);
    if (resetAtMs == null && hours != null && hours >= 0) resetAtMs = epochMs((now + hours * 3600e3) / 1000);
    return {
      id: id,
      engine: engine,
      label: str(item.label) || VENDOR_LABELS[engine],
      account: str(item.account) || 'default',
      available: true,
      unlimited: unlimited,
      pctLeft: pctLeft,
      resetAtMs: resetAtMs,
      projectedPct: projectedPct,
      burnRate: num(item.burn_pct_per_hour),
      expiringUsd: num(item.expiring_usd_estimate),
      expiringTokens: num(item.expiring_tokens_estimate),
      stale: item.stale === true,
    };
  }

  function normalize(payload, nowMs) {
    var now = nowMs == null ? Date.now() : nowMs;
    var seen = Object.create(null);
    var counts = Object.create(null);
    var out = [];
    itemsFromPayload(payload).forEach(function (raw) {
      var item = normalizeItem(raw, now);
      if (!item || seen[item.id]) return;
      // Two accounts of one vendor: keep a stable composite key so both
      // chips render instead of the second overwriting the first.
      seen[item.id] = true;
      counts[item.engine] = (counts[item.engine] || 0) + 1;
      out.push(item);
    });
    out.forEach(function (item) { item.showAccount = counts[item.engine] > 1; });
    out.sort(function (a, b) {
      return VENDOR_ORDER[a.engine] - VENDOR_ORDER[b.engine]
        || a.account.localeCompare(b.account) || a.id.localeCompare(b.id);
    });
    return out;
  }

  function isStale(item, nowMs) {
    var now = nowMs == null ? Date.now() : nowMs;
    return item.stale || (item.resetAtMs != null && item.resetAtMs <= now);
  }

  // Remaining quota drives the colour: green, amber low, red nearly out.
  // Stale and unknown readings stay neutral rather than guessing a risk.
  function riskOf(item, nowMs) {
    if (!item.available || item.pctLeft == null || isStale(item, nowMs)) return 'off';
    if (item.pctLeft <= 10) return 'low';
    if (item.pctLeft <= 30) return 'warn';
    return 'ok';
  }

  function fmtCountdown(msUntil) {
    if (num(msUntil) == null) return '';
    if (msUntil <= 0) return 'now';
    if (msUntil < 60000) return '<1m';
    var mins = Math.ceil(msUntil / 60000);
    if (mins < 60) return mins + 'm';
    var hours = Math.floor(mins / 60);
    if (hours < 24) return hours + 'h' + (mins % 60 ? ' ' + mins % 60 + 'm' : '');
    var days = Math.floor(hours / 24);
    return days + 'd' + (hours % 24 ? ' ' + hours % 24 + 'h' : '');
  }

  function fmtClock(ms) {
    if (num(ms) == null) return '';
    return new Date(ms).toLocaleString([], {
      month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
    });
  }

  function pctText(pct) {
    if (pct == null) return 'Not available';
    if (pct > 0 && pct < 1) return '<1% left';
    if (pct < 100 && pct > 99) return '>99% left';
    return Math.round(pct) + '% left';
  }

  // The small line under each bar shows the reset countdown or provider limits.
  function subText(item, nowMs) {
    var now = nowMs == null ? Date.now() : nowMs;
    if (item.unlimited) return 'Limits vary by provider';
    if (item.resetAtMs != null) {
      return item.resetAtMs <= now ? 'Reset pending' : 'Resets in ' + fmtCountdown(item.resetAtMs - now);
    }
    return 'Reset time unknown';
  }

  function fmtTokens(tokens) {
    return Math.round(tokens).toLocaleString();
  }

  // Full sentence tooltip — plain words for novices, all the numbers the
  // engine reported.
  function buildTooltip(item, nowMs) {
    var now = nowMs == null ? Date.now() : nowMs;
    var label = item.label + (item.account !== 'default' || item.showAccount ? ' (' + item.account + ')' : '');
    var parts = [label + '.'];
    parts.push(item.unlimited ? 'Free routing. Limits vary by provider.'
      : item.pctLeft == null ? 'Usage reading not available.' : item.pctLeft + '% left.');
    if (!item.unlimited && item.resetAtMs != null) {
      var ms = item.resetAtMs - now;
      parts.push('Resets ' + fmtClock(item.resetAtMs)
        + (ms > 0 ? ' (in ' + fmtCountdown(ms) + ').' : '. Reset pending.'));
    }
    if (!item.unlimited && item.projectedPct != null && item.pctLeft != null && !isStale(item, now)) {
      parts.push(item.projectedPct + '% could go unused at this pace.');
      if (item.burnRate != null && item.burnRate >= 0) parts.push(item.burnRate + '% of quota used per hour recently.');
      if (item.expiringUsd != null && item.expiringUsd >= 0.01) {
        parts.push('About $' + item.expiringUsd.toFixed(2) + ' of API-priced work could go unused at this pace. This is not money or a refund.');
      }
      if (item.expiringTokens != null && item.expiringTokens >= 1) {
        parts.push('About ' + fmtTokens(item.expiringTokens) + ' tokens could go unused.');
      }
    }
    if (isStale(item, now)) parts.push('Older reading. Waiting for an update.');
    return parts.join(' ');
  }

  /* ---------------- DOM rendering ---------------- */

  var strip = null;
  var chips = Object.create(null);       // vendor id -> chip element
  var grid = null;
  var lastItems = null;
  var unsupported = false;
  var backoffUntil = 0;
  var inflight = null;
  var booted = false;

  function ensureStrip() {
    if (strip && document.body && document.body.contains(strip)) return strip;
    strip = document.getElementById('headroomBars');
    if (!strip) return null;
    chips = Object.create(null);
    var heading = document.createElement('div');
    heading.className = 'hb-heading';
    heading.textContent = 'Usage left';
    grid = document.createElement('div');
    grid.className = 'hb-grid';
    strip.replaceChildren(heading, grid);
    return strip;
  }

  function chipEl() {
    var el = document.createElement('div');
    el.className = 'hb-chip';
    el.tabIndex = 0;
    var top = document.createElement('div');
    top.className = 'hb-top';
    var name = document.createElement('span');
    name.className = 'hb-name';
    var val = document.createElement('span');
    val.className = 'hb-val';
    top.appendChild(name);
    top.appendChild(val);
    var bar = document.createElement('div');
    bar.className = 'hb-bar';
    bar.setAttribute('aria-hidden', 'true');
    var fill = document.createElement('div');
    fill.className = 'hb-fill';
    bar.appendChild(fill);
    var sub = document.createElement('div');
    sub.className = 'hb-sub';
    el.appendChild(top);
    el.appendChild(bar);
    el.appendChild(sub);
    return el;
  }

  function render(items, nowMs) {
    if (typeof document === 'undefined') return;
    var host = ensureStrip();
    if (!host) return;
    var now = nowMs == null ? Date.now() : nowMs;
    var wanted = Object.create(null);
    (items || []).forEach(function (item, index) {
      var el = chips[item.id];
      if (!el) {
        el = chipEl();
        chips[item.id] = el;
        el.dataset.accountId = item.id;
      }
      var risk = riskOf(item, now);
      el.className = 'hb-chip hb-' + risk + (isStale(item, now) ? ' hb-stale' : '');
      el.title = buildTooltip(item, now);
      el.setAttribute('role', item.pctLeft == null ? 'group' : 'meter');
      el.setAttribute('aria-label', el.title);
      if (item.pctLeft != null) {
        el.setAttribute('aria-valuemin', '0');
        el.setAttribute('aria-valuemax', '100');
        el.setAttribute('aria-valuenow', String(item.pctLeft));
        el.setAttribute('aria-valuetext', pctText(item.pctLeft) + '. ' + subText(item, now));
      } else {
        ['aria-valuemin', 'aria-valuemax', 'aria-valuenow', 'aria-valuetext'].forEach(function (attr) { el.removeAttribute(attr); });
      }
      el.querySelector('.hb-name').textContent = item.label + (item.showAccount ? ' · ' + item.account : '');
      el.querySelector('.hb-val').textContent = item.unlimited ? 'Free routing' : pctText(item.pctLeft);
      var fill = el.querySelector('.hb-fill');
      fill.style.width = item.pctLeft == null ? '0%' : item.pctLeft + '%';
      el.querySelector('.hb-sub').textContent = subText(item, now);
      wanted[item.id] = el;
      if (grid.children[index] !== el) grid.insertBefore(el, grid.children[index] || null);
    });
    Object.keys(chips).forEach(function (id) {
      if (!wanted[id]) {
        chips[id].remove();
        delete chips[id];
      }
    });
    // Keep DOM order == item order (cheap: <10 nodes).
    host.hidden = !items || !items.length;
  }

  function tickCountdowns() {
    if (document.hidden || !lastItems || !strip || strip.hidden) return;
    // Only the countdown text and tooltip age between polls; re-render the
    // cached model (no fetch, no layout-heavy work).
    render(lastItems);
  }

  function poll() {
    if (unsupported || typeof fetch === 'undefined') return Promise.resolve();
    if (inflight) return inflight;
    if (typeof document !== 'undefined' && document.hidden) return Promise.resolve();
    if (Date.now() < backoffUntil) return Promise.resolve();
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 10000);
    inflight = Promise.resolve().then(function () {
      return fetch('/api/headroom', { cache: 'no-store', signal: controller.signal });
    }).then(function (r) {
      if (r.status === 404) {
        unsupported = true;
        lastItems = null;
        render([]);
        return null;
      }
      if (!r.ok) throw new Error('http ' + r.status);
      return r.json();
    }).then(function (data) {
      if (unsupported) return;
      if (!data || data.ok !== true || !Array.isArray(data.rows)) throw new Error('invalid headroom');
      backoffUntil = 0;
      lastItems = normalize(data);
      render(lastItems);
    }).catch(function () {
      backoffUntil = Date.now() + ERROR_BACKOFF_MS;
      if (lastItems) {
        lastItems = lastItems.map(function (item) { return Object.assign({}, item, { stale: true }); });
        render(lastItems);
      }
    }).finally(function () {
      clearTimeout(timeout);
      inflight = null;
    });
    return inflight;
  }

  function boot() {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', boot, { once: true });
      return;
    }
    if (booted || !ensureStrip()) return;
    booted = true;
    strip.hidden = true;
    poll();
    setInterval(poll, POLL_MS);
    setInterval(tickCountdowns, TICK_MS);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) poll();
    });
  }

  return {
    boot: boot,
    poll: poll,
    render: render,
    normalize: normalize,
    normalizeItem: normalizeItem,
    riskOf: riskOf,
    subText: subText,
    buildTooltip: buildTooltip,
    fmtCountdown: fmtCountdown,
    fmtClock: fmtClock,
    _itemsFromPayload: itemsFromPayload,
  };
});
