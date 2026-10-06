/* Savings UI (L13): header ticker, per-session value chips, savings panel,
 * milestone celebrations.
 *
 * Data comes from GET /api/savings (the savings engine). When that endpoint
 * is not present yet (404) the whole surface stays hidden: no ticker, no
 * panel, and row chips keep working off the session fields they already have
 * (cost_usd, runtime). Sounds, confetti and count-up ride window.cccFx when
 * it exists and degrade to silent/no-op fallbacks when it does not.
 */

// SAVINGS_UI_START — pure helpers, no DOM access. tests/test_savings_ui.py
// extracts this block and executes it in node, so keep it free of window,
// document, fetch, and localStorage.
function cccSavFmtUsd(n) {
  const v = Number(n);
  if (!isFinite(v) || v <= 0) return '$0.00';
  // >= $1,000 drops the cents and groups; the 999.995 boundary catches values
  // like 999.999 that would otherwise render as an ungrouped "1000.00".
  if (v >= 999.995) return '$' + Math.round(v).toLocaleString('en-US');
  return '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function cccSavFmtTokens(n) {
  const v = Number(n) || 0;
  if (v >= 1e9) return (v / 1e9).toFixed(v >= 1e10 ? 0 : 1) + 'B';
  if (v >= 1e6) return (v / 1e6).toFixed(v >= 1e7 ? 0 : 1) + 'M';
  if (v >= 1e3) return Math.round(v / 1e3) + 'k';
  return String(Math.round(v));
}

const CCC_SAV_MILESTONES = [
  { id: 'work-10',   kind: 'work',   at: 10,        short: '$10',   label: '$10 of agent work' },
  { id: 'work-100',  kind: 'work',   at: 100,       short: '$100',  label: '$100 of agent work' },
  { id: 'work-1000', kind: 'work',   at: 1000,      short: '$1,000', label: '$1,000 of agent work' },
  { id: 'tok-1b',    kind: 'tokens', at: 1000000000, short: '1B',    label: '1 billion free-model tokens' },
];

function cccSavMetric(data, kind) {
  if (!data || typeof data !== 'object') return 0;
  return kind === 'tokens' ? (Number(data.free_tokens) || 0) : (Number(data.api_value_usd) || 0);
}

// Milestones already reached by an /api/savings payload.
function cccSavReached(data) {
  return CCC_SAV_MILESTONES.filter(m => cccSavMetric(data, m.kind) >= m.at);
}

// The next milestone still ahead, with 0..1 progress toward it.
function cccSavNext(data) {
  for (const m of CCC_SAV_MILESTONES) {
    const v = cccSavMetric(data, m.kind);
    if (v < m.at) return { milestone: m, pct: m.at > 0 ? Math.min(1, v / m.at) : 1 };
  }
  return null;
}

// Per-session chip model. Free runs (L04 marks the row runtime:"free") get the
// "$0 · saved $X" story: the session's cost_usd is what the same work would
// have cost at API prices. Other rows show the API-list value of the work so
// a novice sees every session in dollars, not just the free ones. Null when
// there is no honest number to show.
function cccSavChip(c) {
  if (!c || typeof c !== 'object') return null;
  const cost = Number(c.cost_usd);
  const hasCost = isFinite(cost) && cost > 0.004;
  if (String(c.runtime || '') === 'free') {
    return hasCost
      ? {
          cls: 'is-free',
          text: '$0 · saved ' + cccSavFmtUsd(cost),
          tip: 'This session runs on a free model. At API prices the same work would cost about ' + cccSavFmtUsd(cost) + '.',
        }
      : {
          cls: 'is-free',
          text: '$0',
          tip: 'This session runs on a free model. It costs you nothing.',
        };
  }
  if (!hasCost) return null;
  return {
    cls: 'is-api',
    text: cccSavFmtUsd(cost) + ' value',
    tip: 'Your agent did about ' + cccSavFmtUsd(cost) + ' of work at API prices.',
  };
}

// Ticker pill model: the headline number is the API-priced value of today's
// agent work (it always grows, which is what makes the coin-count delightful).
// free_saved_usd rides along so the tooltip and the gold styling can tell the
// "and it cost $0" half of the story.
function cccSavTicker(data, rangeLabel) {
  const usd = cccSavMetric(data, 'work');
  const free = Number(data && data.free_saved_usd) || 0;
  const label = rangeLabel || 'today';
  return {
    usd,
    text: cccSavFmtUsd(usd),
    free: free > 0 ? cccSavFmtUsd(free) : null,
    tip: 'Your agents did ' + cccSavFmtUsd(usd) + ' of work ' + label
      + (free > 0 ? '. ' + cccSavFmtUsd(free) + ' of it ran on free models' : '')
      + '. Click for your savings report.',
  };
}

// One-line "and it was worth it" under the hero number. Prefers the free-model
// story, falls back to plan ROI, then to nothing (the tiles carry the rest).
function cccSavSubline(data) {
  if (!data || typeof data !== 'object') return '';
  const free = Number(data.free_saved_usd) || 0;
  const runs = Number(data.free_runs) || 0;
  if (free > 0) {
    return cccSavFmtUsd(free) + ' of it ran on free models'
      + (runs > 0 ? ' across ' + runs + (runs === 1 ? ' run' : ' runs') : '')
      + '. You paid $0 for that.';
  }
  const roi = Number(data.roi_x) || 0;
  const plan = Number(data.plan_cost_usd) || 0;
  if (roi >= 2 && plan > 0) return 'That is a ' + (roi >= 10 ? Math.round(roi) : roi.toFixed(1)) + 'x return on your plan.';
  return '';
}

function cccSavRangeTabLabel(range) {
  return { today: 'Today', week: 'Week', month: 'Month', all: 'All time' }[range] || 'Today';
}

function cccSavRangePhrase(range) {
  return { today: 'today', week: 'this week', month: 'this month', all: 'so far' }[range] || 'today';
}
// SAVINGS_UI_END

(function () {
  'use strict';
  if (window.cccSavings) return;

  const TICKER_POLL_MS = 60000;       // today's number while the tab is open
  const MISS_POLL_MS = 300000;        // endpoint absent: slow probe until it lands
  const ALL_RANGE_EVERY = 5;          // fetch range=all every Nth tick (milestones)
  const LS_TODAY = 'ccc.savings.today.v1';
  const LS_CELEBRATED = 'ccc.savings.celebrated.v1';
  const LS_RANGE = 'ccc.savings.range.v1';

  let _ticker = null;                 // the pill element in #sidebarTopAlerts
  let _tickerSeen = false;            // has one successful render happened
  let _lastUsd = null;                // last rendered ticker value
  let _allRange = null;               // latest range=all payload (milestones)
  let _panel = null;                  // lazily built savings panel overlay
  let _cheer = null;                  // lazily built milestone celebration overlay
  let _tickCount = 0;
  let _apiUp = null;                  // null = unknown, true/false after a fetch
  let _timer = null;
  let _celebrated = null;             // Set of milestone ids already celebrated
  let _countRaf = 0;

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function lsGet(key) {
    try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch (_) { return null; }
  }
  function lsSet(key, val) {
    try { localStorage.setItem(key, JSON.stringify(val)); } catch (_) {}
  }

  function reducedMotion() {
    if (window.cccFx && typeof window.cccFx.reducedMotion === 'function') {
      try { return !!window.cccFx.reducedMotion(); } catch (_) {}
    }
    try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (_) { return false; }
  }

  function playSound(name) {
    if (!window.cccFx || typeof window.cccFx.play !== 'function') return;
    try { window.cccFx.play(name); } catch (_) {}
  }

  function confetti(opts) {
    if (!window.cccFx || typeof window.cccFx.confetti !== 'function') return;
    if (reducedMotion()) return;
    try { window.cccFx.confetti(opts); } catch (_) {}
  }

  // Count-up to a money label inside el. Uses cccFx.countUp when present,
  // otherwise a small rAF ease-out. Reduced motion jumps straight to done.
  function countUpMoney(el, from, to, ms) {
    if (!el) return;
    const dur = ms || 900;
    const done = cccSavFmtUsd(to);
    if (reducedMotion() || !isFinite(from) || !isFinite(to) || from === to) {
      el.textContent = done;
      return;
    }
    if (window.cccFx && typeof window.cccFx.countUp === 'function') {
      try { window.cccFx.countUp(el, to, { from: from, format: cccSavFmtUsd, duration: dur }); return; } catch (_) {}
    }
    if (_countRaf) cancelAnimationFrame(_countRaf);
    const t0 = performance.now();
    const step = (t) => {
      const p = Math.min(1, (t - t0) / dur);
      const e = 1 - Math.pow(1 - p, 3);
      el.textContent = cccSavFmtUsd(from + (to - from) * e);
      if (p < 1) _countRaf = requestAnimationFrame(step);
      else { el.textContent = done; _countRaf = 0; }
    };
    _countRaf = requestAnimationFrame(step);
  }

  function fetchSavings(range) {
    return fetch('/api/savings?range=' + encodeURIComponent(range), { cache: 'no-store' })
      .then(r => (r.ok ? r.json() : null))
      .catch(() => null);
  }

  // ── Ticker pill ────────────────────────────────────────────────────────
  function mountTicker() {
    if (_ticker) return _ticker;
    const host = document.getElementById('sidebarTopAlerts');
    if (!host) return null;
    const b = document.createElement('button');
    b.type = 'button';
    b.id = 'cccSavingsTicker';
    b.className = 'ccc-sav-ticker';
    b.hidden = true;
    b.innerHTML = '<span class="ccc-sav-ticker-spark" aria-hidden="true">&#10022;</span>'
      + '<span class="ccc-sav-ticker-num">$0.00</span>';
    b.addEventListener('click', openPanel);
    host.appendChild(b);
    _ticker = b;
    return b;
  }

  function renderTicker(data) {
    const el = mountTicker();
    if (!el) return;
    const model = cccSavTicker(data, 'today');
    const numEl = el.querySelector('.ccc-sav-ticker-num');
    const today = new Date().toISOString().slice(0, 10);
    const stored = lsGet(LS_TODAY);

    if (!_tickerSeen) {
      // First paint: count up from this morning's stored value (same day) so a
      // reload feels continuous. No coin on first paint.
      const from = stored && stored.day === today ? (Number(stored.usd) || 0) : 0;
      countUpMoney(numEl, Math.min(from, model.usd), model.usd, 1200);
    } else if (_lastUsd != null && model.usd > _lastUsd + 0.0001) {
      // A live increase: count up, coin sound, brief glow. Drops (new day,
      // recalibration) snap silently instead of counting backwards.
      countUpMoney(numEl, _lastUsd, model.usd, 900);
      playSound('coin');
      el.classList.remove('is-bump');
      void el.offsetWidth; // restart the glow animation
      el.classList.add('is-bump');
    } else {
      numEl.textContent = model.text;
    }

    el.title = model.tip;
    el.classList.toggle('is-free', !!model.free);
    el.hidden = false;
    _tickerSeen = true;
    _lastUsd = model.usd;
    lsSet(LS_TODAY, { day: today, usd: model.usd });
  }

  // ── Row chip (called by app.js for every conv row) ─────────────────────
  function rowChipHtml(c) {
    const chip = cccSavChip(c);
    if (!chip) return '';
    return '<span class="ccc-sav-chip ' + chip.cls + '" title="' + esc(chip.tip) + '">'
      + esc(chip.text) + '</span>';
  }

  // ── Milestones ─────────────────────────────────────────────────────────
  function celebratedSet() {
    if (_celebrated) return _celebrated;
    _celebrated = new Set(Array.isArray(lsGet(LS_CELEBRATED)) ? lsGet(LS_CELEBRATED) : []);
    return _celebrated;
  }

  function checkMilestones(data) {
    if (!data) return;
    const done = celebratedSet();
    const fresh = cccSavReached(data).filter(m => !done.has(m.id));
    if (!fresh.length) return;
    fresh.forEach(m => done.add(m.id));
    lsSet(LS_CELEBRATED, Array.from(done));
    celebrate(fresh[fresh.length - 1]); // show the biggest new one
  }

  function celebrate(m) {
    const ov = cheerOverlay();
    ov.querySelector('.ccc-sav-cheer-big').textContent = m.short + '!';
    ov.querySelector('.ccc-sav-cheer-line').textContent = 'Your agents just crossed ' + m.label + '.';
    ov.querySelector('.ccc-sav-cheer-sub').textContent = m.kind === 'tokens'
      ? 'All of it on free models. That is serious compute for $0.'
      : 'That kind of work adds up fast. Worth telling someone.';
    ov.classList.add('open');
    const btn = ov.querySelector('.ccc-sav-cheer-ok');
    if (btn) btn.focus();
    confetti({ spread: 80 });
    playSound('success');
    if (typeof window.cccNotify === 'function') {
      try { window.cccNotify({ title: 'Savings milestone: ' + m.short, body: 'Your agents crossed ' + m.label + '.', kind: 'savings' }); } catch (_) {}
    }
  }

  function cheerOverlay() {
    if (_cheer) return _cheer;
    const ov = document.createElement('div');
    ov.className = 'upd-overlay ccc-sav-overlay ccc-sav-cheer';
    ov.setAttribute('role', 'dialog');
    ov.setAttribute('aria-modal', 'true');
    ov.innerHTML = ''
      + '<div class="upd-backdrop" data-sav-cheer-close></div>'
      + '<div class="upd-dialog ccc-sav-cheer-dialog">'
      +   '<div class="ccc-sav-cheer-pop" aria-hidden="true">&#10022;</div>'
      +   '<div class="ccc-sav-cheer-big">$100!</div>'
      +   '<div class="ccc-sav-cheer-line"></div>'
      +   '<div class="ccc-sav-cheer-sub"></div>'
      +   '<div class="ccc-sav-cheer-btns">'
      +     '<button type="button" class="upd-btn upd-primary ccc-sav-cheer-share">Share your savings</button>'
      +     '<button type="button" class="upd-btn ccc-sav-cheer-ok" data-sav-cheer-close>Keep going</button>'
      +   '</div>'
      + '</div>';
    ov.addEventListener('click', (e) => {
      if (e.target.closest('[data-sav-cheer-close]')) ov.classList.remove('open');
    });
    ov.querySelector('.ccc-sav-cheer-share').addEventListener('click', () => {
      ov.classList.remove('open');
      openShareCard();
    });
    document.body.appendChild(ov);
    _cheer = ov;
    return ov;
  }

  function openShareCard() {
    window.open('/throughput.html?share=1', '_blank');
  }

  // ── Savings panel ──────────────────────────────────────────────────────
  function panelOverlay() {
    if (_panel) return _panel;
    const ov = document.createElement('div');
    ov.className = 'upd-overlay ccc-sav-overlay ccc-sav-panel';
    ov.setAttribute('role', 'dialog');
    ov.setAttribute('aria-modal', 'true');
    ov.setAttribute('aria-labelledby', 'cccSavPanelTitle');
    ov.innerHTML = ''
      + '<div class="upd-backdrop" data-sav-close></div>'
      + '<div class="upd-dialog ccc-sav-dialog">'
      +   '<div class="ccc-sav-head">'
      +     '<div class="upd-title" id="cccSavPanelTitle">Your savings</div>'
      +     '<button type="button" class="settings-modal-close" aria-label="Close" data-sav-close>&times;</button>'
      +   '</div>'
      +   '<div class="ccc-sav-tabs" role="tablist">'
      +     ['today', 'week', 'month', 'all'].map(r =>
          '<button type="button" role="tab" class="ccc-sav-tab" data-range="' + r + '">'
          + esc(cccSavRangeTabLabel(r)) + '</button>').join('')
      +   '</div>'
      +   '<div class="ccc-sav-body">'
      +     '<div class="ccc-sav-hero">'
      +       '<div class="ccc-sav-hero-num">$0.00</div>'
      +       '<div class="ccc-sav-hero-cap">of agent work today</div>'
      +       '<div class="ccc-sav-hero-sub"></div>'
      +     '</div>'
      +     '<div class="ccc-sav-tiles"></div>'
      +     '<div class="ccc-sav-miles-wrap">'
      +       '<div class="ccc-sav-miles-title">Milestones</div>'
      +       '<div class="ccc-sav-miles"></div>'
      +     '</div>'
      +     '<div class="ccc-sav-empty" hidden>'
      +       'No savings counted yet. Send your agent a first task and watch this fill up.'
      +     '</div>'
      +   '</div>'
      +   '<div class="ccc-sav-foot">'
      +     '<button type="button" class="upd-btn upd-primary ccc-sav-share">Share your savings</button>'
      +     '<a class="upd-btn ccc-sav-tput" href="/throughput.html" target="_blank" rel="noopener">Token details</a>'
      +   '</div>'
      +   '<div class="ccc-sav-note">Work is priced at API list rates, so this is what the same output would cost per token.</div>'
      + '</div>';
    ov.addEventListener('click', (e) => {
      if (e.target.closest('[data-sav-close]') || e.target.classList.contains('upd-backdrop')) closePanel();
      const tab = e.target.closest('.ccc-sav-tab');
      if (tab) selectRange(tab.dataset.range);
    });
    ov.querySelector('.ccc-sav-share').addEventListener('click', openShareCard);
    document.body.appendChild(ov);
    _panel = ov;
    return ov;
  }

  function selectedRange() {
    const r = lsGet(LS_RANGE);
    return ['today', 'week', 'month', 'all'].includes(r && r.range) ? r.range : 'today';
  }

  function tile(label, value, sub, cls) {
    return '<div class="ccc-sav-tile ' + (cls || '') + '">'
      + '<div class="ccc-sav-tile-val">' + esc(value) + '</div>'
      + '<div class="ccc-sav-tile-label">' + esc(label) + '</div>'
      + (sub ? '<div class="ccc-sav-tile-sub">' + esc(sub) + '</div>' : '')
      + '</div>';
  }

  function renderPanel(range, data, allData) {
    const ov = panelOverlay();
    const phrase = cccSavRangePhrase(range);
    ov.querySelectorAll('.ccc-sav-tab').forEach(t => {
      const on = t.dataset.range === range;
      t.classList.toggle('is-on', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });

    const body = ov.querySelector('.ccc-sav-body');
    const empty = ov.querySelector('.ccc-sav-empty');
    const usd = cccSavMetric(data, 'work');
    const hasAny = data && (usd > 0 || (Number(data.free_runs) || 0) > 0 || (Number(data.sessions) || 0) > 0);
    body.classList.toggle('is-empty', !hasAny);
    empty.hidden = !!hasAny;

    if (hasAny) {
      const numEl = ov.querySelector('.ccc-sav-hero-num');
      const shown = numEl.dataset.usd ? Number(numEl.dataset.usd) : 0;
      countUpMoney(numEl, Math.min(shown, usd), usd, 800);
      numEl.dataset.usd = usd;
      ov.querySelector('.ccc-sav-hero-cap').textContent = 'of agent work ' + phrase;
      ov.querySelector('.ccc-sav-hero-sub').textContent = cccSavSubline(data);

      const free = Number(data.free_saved_usd) || 0;
      const runs = Number(data.free_runs) || 0;
      const sessions = Number(data.sessions) || 0;
      const plan = Number(data.plan_cost_usd) || 0;
      const roi = Number(data.roi_x) || 0;
      const toks = Number(data.free_tokens) || 0;
      let tiles = tile('agent work', cccSavFmtUsd(usd), 'at API prices', 'is-hero');
      if (free > 0 || runs > 0) {
        tiles += tile('on free models', cccSavFmtUsd(free), runs > 0 ? runs + (runs === 1 ? ' run' : ' runs') + ' at $0' : 'at $0', 'is-free');
      }
      if (sessions > 0) tiles += tile('sessions', String(sessions), '', '');
      if (toks > 0) tiles += tile('free tokens', cccSavFmtTokens(toks), '', '');
      if (plan > 0) tiles += tile('your plan', cccSavFmtUsd(plan), roi >= 2 ? (roi >= 10 ? Math.round(roi) : roi.toFixed(1)) + 'x return' : '', '');
      ov.querySelector('.ccc-sav-tiles').innerHTML = tiles;
    }

    // Milestone ladder always renders from all-time data (falls back to the
    // current range when the all-time fetch has not landed yet).
    const basis = allData || data;
    const reached = new Set(cccSavReached(basis).map(m => m.id));
    const next = cccSavNext(basis);
    ov.querySelector('.ccc-sav-miles').innerHTML = CCC_SAV_MILESTONES.map(m => {
      const hit = reached.has(m.id);
      const isNext = next && next.milestone.id === m.id;
      const pct = isNext ? Math.round(next.pct * 100) : (hit ? 100 : 0);
      return '<div class="ccc-sav-mile' + (hit ? ' is-hit' : '') + (isNext ? ' is-next' : '') + '">'
        + '<span class="ccc-sav-mile-mark" aria-hidden="true">' + (hit ? '&#10003;' : '&#9675;') + '</span>'
        + '<span class="ccc-sav-mile-label">' + esc(m.label) + '</span>'
        + '<span class="ccc-sav-mile-track"><span class="ccc-sav-mile-fill" style="width:' + pct + '%"></span></span>'
        + (isNext && !hit ? '<span class="ccc-sav-mile-pct">' + pct + '%</span>' : '')
        + '</div>';
    }).join('');
  }

  function selectRange(range) {
    lsSet(LS_RANGE, { range });
    fetchSavings(range).then(d => {
      if (d) { _apiUp = true; renderPanel(range, d, _allRange); }
      else renderPanel(range, null, _allRange);
    });
  }

  function openPanel() {
    const ov = panelOverlay();
    ov.classList.add('open');
    const range = selectedRange();
    selectRange(range);
    if (!_allRange) {
      fetchSavings('all').then(d => {
        _allRange = d;
        // Re-render the open tab so the milestone ladder gets all-time data.
        if (_panel && _panel.classList.contains('open')) selectRange(selectedRange());
      });
    }
    const closeBtn = ov.querySelector('.settings-modal-close');
    if (closeBtn) closeBtn.focus();
  }

  function closePanel() {
    if (_panel) _panel.classList.remove('open');
  }

  // ── Polling ────────────────────────────────────────────────────────────
  function tick() {
    if (document.hidden) { schedule(); return; }
    _tickCount += 1;
    fetchSavings('today').then(d => {
      if (d) {
        _apiUp = true;
        renderTicker(d);
      } else {
        _apiUp = false;
        // Endpoint not there yet: keep the pill hidden, probe slowly.
        if (_ticker) _ticker.hidden = true;
      }
      schedule();
    });
    if (_tickCount % ALL_RANGE_EVERY === 1) {
      fetchSavings('all').then(d => {
        if (d) { _allRange = d; checkMilestones(d); }
      });
    }
  }

  function schedule() {
    if (_timer) clearTimeout(_timer);
    _timer = setTimeout(tick, _apiUp === false ? MISS_POLL_MS : TICKER_POLL_MS);
  }

  function refresh() { tick(); }

  // Escape closes whichever savings surface is open.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (_cheer && _cheer.classList.contains('open')) _cheer.classList.remove('open');
    else if (_panel && _panel.classList.contains('open')) closePanel();
  });

  // Waking the tab is a good moment to catch up on missed savings.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && _apiUp) tick();
  });

  window.cccSavings = { rowChipHtml, open: openPanel, refresh };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tick);
  } else {
    tick();
  }
})();
/* Savings page (/savings.html) — renders GET /api/savings and drives the
 * editable plan cost via POST /api/savings/plan. Dependency-free; the page is
 * a standalone satellite (same pattern as spawn-ledger.html). */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var currentRange = "today";
  var inFlight = null;   // AbortController for the pending range fetch

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function fmtUsd(v, digits) {
    var n = Number(v);
    if (!isFinite(n)) return "$0.00";
    var d = digits == null ? (Math.abs(n) >= 1000 ? 0 : 2) : digits;
    return "$" + n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  }

  function fmtTokens(n) {
    n = Number(n) || 0;
    if (n >= 1e9) return (n / 1e9).toFixed(1) + "B";
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
    return String(Math.round(n));
  }

  function fmtRoi(x) {
    if (x == null || !isFinite(Number(x))) return "–";
    return Number(x).toLocaleString(undefined, { maximumFractionDigits: 1 }) + "×";
  }

  function shortDay(iso) {
    var d = new Date(iso + "T12:00:00");
    if (isNaN(d.getTime())) return iso.slice(5);
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
  }

  function showErr(msg) {
    var el = $("errPill");
    el.hidden = !msg;
    el.textContent = msg || "";
  }

  function renderRangeButtons() {
    var btns = $("ranges").querySelectorAll("button");
    for (var i = 0; i < btns.length; i++) {
      btns[i].classList.toggle("on", btns[i].dataset.range === currentRange);
    }
  }

  function renderHero(p) {
    $("apiValue").textContent = fmtUsd(p.api_value_usd);
    var sub = "priced at API list rates";
    if (p.estimated_share_usd > 0) {
      sub += " · " + fmtUsd(p.estimated_share_usd) + " estimated";
    }
    $("apiValueSub").textContent = sub;

    $("planCost").textContent = fmtUsd(p.plan_cost_usd);
    var planName = (p.plan && p.plan.name) || "plan";
    $("planCostSub").textContent = planName +
      (p.plan && p.plan.source === "default" ? " (default)" : "") +
      " · " + (p.label || "").toLowerCase();

    $("roi").textContent = fmtRoi(p.roi_x);
    $("roiSub").textContent = p.roi_x != null
      ? "each plan dollar did " + fmtUsd(p.plan_cost_usd > 0 ? p.api_value_usd / p.plan_cost_usd : 0) + " of work"
      : "add a plan cost to see ROI";

    $("freeSaved").textContent = fmtUsd(p.free_saved_usd);
    var bits = [];
    if (p.free_runs) bits.push(p.free_runs + " free run" + (p.free_runs === 1 ? "" : "s"));
    if (p.free_tokens) bits.push(fmtTokens(p.free_tokens) + " tokens");
    if (p.router_connected) bits.push("router connected");
    $("freeSavedSub").textContent = bits.length ? bits.join(" · ") : "no free runs yet";

    // Value vs plan bar: paid-blue = plan cost share, accent = extra value.
    var plan = Number(p.plan_cost_usd) || 0;
    var value = Number(p.api_value_usd) || 0;
    var free = Math.min(Number(p.free_saved_usd) || 0, value);
    var vs = $("vsBar");
    if (plan > 0 || value > 0) {
      vs.hidden = false;
      var total = Math.max(plan, value);
      // The track shows plan cost vs delivered value as two stacked shares:
      // blue = the plan's share of the bar, teal = the free-run portion inside
      // the delivered value.
      var paidPct = Math.min(plan / total, 1) * 100;
      var freePct = Math.min(free / total, 1) * 100;
      $("fillPaid").style.width = paidPct + "%";
      $("fillFree").style.width = freePct + "%";
      $("lgValue").textContent = fmtUsd(value);
      $("lgFree").textContent = fmtUsd(free);
      $("lgPlan").textContent = fmtUsd(plan);
    } else {
      vs.hidden = true;
    }
  }

  function renderDays(p) {
    var wrap = $("days");
    var days = p.by_day || [];
    if (!days.length) {
      wrap.innerHTML = "";
      $("daysEmpty").hidden = false;
      return;
    }
    $("daysEmpty").hidden = true;
    var max = 0;
    for (var i = 0; i < days.length; i++) {
      max = Math.max(max, days[i].api_value_usd || 0);
    }
    // Cap at 21 columns so a long window stays readable; newest days win.
    var shown = days.slice(-21);
    wrap.innerHTML = shown.map(function (d) {
      var h = max > 0 ? Math.max((d.api_value_usd / max) * 100, 2) : 0;
      var fh = d.api_value_usd > 0 ? Math.min((d.free_saved_usd || 0) / d.api_value_usd, 1) * 100 : 0;
      return '<div class="day" title="' + esc(d.day) + ": " + esc(fmtUsd(d.api_value_usd)) +
        " value" + (d.free_saved_usd > 0 ? ", " + esc(fmtUsd(d.free_saved_usd)) + " free" : "") + '">' +
        '<div class="bar" style="height:' + h.toFixed(1) + '%">' +
        (fh > 0 ? '<div class="free" style="height:' + fh.toFixed(0) + '%"></div>' : "") +
        '</div><div class="lbl">' + esc(shortDay(d.day)) + "</div></div>";
    }).join("");
  }

  function renderModels(p) {
    var models = p.models || [];
    $("modelsWrap").hidden = !models.length;
    $("modelsEmpty").hidden = !!models.length;
    $("modelsBody").innerHTML = models.map(function (m) {
      return "<tr><td>" + esc(m.label || m.model) + "</td><td class=\"num mono\">" +
        esc(fmtTokens(m.tokens)) + "</td><td class=\"num\">" + esc(fmtUsd(m.cost_usd)) + "</td></tr>";
    }).join("");
  }

  function renderPlan(p) {
    var plan = p.plan || {};
    var input = $("planInput");
    if (document.activeElement !== input) {
      input.value = plan.monthly_usd != null ? plan.monthly_usd : "";
    }
    var names = (plan.plans || []).map(function (pl) {
      return pl.name + " $" + pl.monthly_usd + "/mo";
    }).join(" + ");
    $("planNote").textContent = plan.source === "configured"
      ? "Configured plans: " + (names || plan.name) + ". Changing this only adjusts the math here; nothing is billed."
      : "Default: Claude Max at $200/mo. Change it to match what you actually pay; this only adjusts the math here.";
  }

  function render(p) {
    $("partialPill").hidden = !p.partial;
    renderHero(p);
    renderDays(p);
    renderModels(p);
    renderPlan(p);
    $("updated").textContent = p.updated_at
      ? "Counted " + (p.sessions || 0) + " session" + (p.sessions === 1 ? "" : "s") +
        " · updated " + new Date(p.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : "";
  }

  async function load(range, refresh) {
    if (inFlight) inFlight.abort();
    var ctl = new AbortController();
    inFlight = ctl;
    var url = "/api/savings?range=" + encodeURIComponent(range) + (refresh ? "&refresh=1" : "");
    try {
      var res = await fetch(url, { cache: "no-store", signal: ctl.signal });
      var data = await res.json();
      if (ctl !== inFlight) return;          // superseded by a newer click
      if (!res.ok || data.ok === false) {
        showErr(data.error || "savings endpoint returned " + res.status);
        return;
      }
      showErr(null);
      render(data);
    } catch (e) {
      if (e && e.name === "AbortError") return;
      if (ctl === inFlight) showErr("could not load savings");
    }
  }

  function setPlanMsg(text, ok) {
    var el = $("planMsg");
    el.textContent = text || "";
    el.className = "plan-msg " + (ok ? "ok" : "err");
  }

  async function postPlan(body) {
    setPlanMsg("saving…", true);
    try {
      var res = await fetch("/api/savings/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign({ range: currentRange }, body)),
      });
      var data = await res.json();
      if (!res.ok || data.ok === false) {
        setPlanMsg(data.error || "could not save", false);
        return;
      }
      setPlanMsg("saved", true);
      if (data.savings) render(data.savings);
    } catch (e) {
      setPlanMsg("could not save", false);
    }
  }

  $("ranges").addEventListener("click", function (e) {
    var btn = e.target.closest("button[data-range]");
    if (!btn || btn.dataset.range === currentRange) return;
    currentRange = btn.dataset.range;
    renderRangeButtons();
    load(currentRange);
  });

  $("planSave").addEventListener("click", function () {
    var v = parseFloat($("planInput").value);
    if (!isFinite(v) || v < 0) {
      setPlanMsg("enter a monthly amount like 200", false);
      return;
    }
    postPlan({ monthly_usd: v });
  });

  $("planReset").addEventListener("click", function () {
    postPlan({ reset: true });
  });

  renderRangeButtons();
  load(currentRange);
}());
