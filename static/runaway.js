/* Runaway sessions: sessions that keep waking themselves (timers, goals,
 * WatchTower pings) long after a human last spoke to them, with nothing
 * changing. GET /api/runaway lists them (see ccc_server/runaway.py); this
 * card at the top of the window shows each with Open / Hold / Let it run
 * 24h / Dismiss. Hold (ccc_server/runaway_hold.py) stops the session waking
 * itself until Release; held sessions stay on the card (GET
 * /api/runaway/holds) so Release is always one click away.
 *
 * Gate: pop-up id 'runaway-session' (static/popups.js).
 */
(function () {
  'use strict';

  var POLL_MS = 5 * 60 * 1000;
  var sessions = [], held = [], cssLoaded = false;

  function el(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function post(url, body) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (res) { return res.json().catch(function () { return {}; }); });
  }
  function toast(text, kind) { if (typeof window.showOpToast === 'function') window.showOpToast(text, kind || 'info'); }
  function project(cwd) {
    var parts = String(cwd || '').split('/').filter(Boolean);
    return parts.length ? parts[parts.length - 1] : '';
  }
  function tokens(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return Math.round(n / 1e3) + 'k';
    return String(n || 0);
  }
  var TRIGGER_NAMES = { watchtower: 'WatchTower pings', timer: 'timer wakes', goal: 'goal continuations',
    peer: 'session messages', task: 'background tasks', system: 'system notices', unknown: 'other' };
  function drivers(triggers) {
    return Object.keys(triggers || {}).sort(function (a, b) { return triggers[b] - triggers[a]; })
      .slice(0, 2).map(function (k) { return triggers[k] + ' ' + (TRIGGER_NAMES[k] || k); }).join(', ');
  }
  function since(ts) {
    if (!ts) return 'no human turn on record';
    var h = (Date.now() / 1000 - ts) / 3600;
    return 'you last spoke ' + (h < 1 ? Math.round(h * 60) + 'm' : h < 48 ? Math.round(h) + 'h' : Math.round(h / 24) + 'd') + ' ago';
  }
  function summary(s) {
    var idle = s.stagnant_turns + ' in a row changed nothing';
    if (s.noop_turns) idle += ' (' + s.noop_turns + ' self-reported no-op)';
    return s.auto_turns + ' turns over ' + s.auto_hours + 'h on its own · ' + idle + ' · ≈' + tokens(s.cost_tokens) + ' tokens';
  }

  function render() {
    var card = el('cccRunaway');
    if (!(sessions.length || held.length) || !window.cccPopups || !window.cccPopups.allowed('runaway-session')) {
      if (card) card.hidden = true;
      return;
    }
    if (!cssLoaded) {
      var css = document.createElement('link'); css.rel = 'stylesheet'; css.href = '/static/runaway.css'; document.head.appendChild(css);
      cssLoaded = true;
    }
    if (!card) {
      card = document.createElement('section');
      card.id = 'cccRunaway'; card.className = 'rw-card';
      card.setAttribute('role', 'status');
      card.addEventListener('click', onClick);
      document.body.appendChild(card);
    }
    var heldIds = {};
    held.forEach(function (h) { heldIds[h.session_id] = h; });
    var flagged = sessions.filter(function (s) { return !heldIds[s.session_id]; });
    var titles = {};
    sessions.forEach(function (s) { titles[s.session_id] = s; });
    var n = flagged.length;
    var title = n ? n + ' session' + (n === 1 ? ' keeps' : 's keep') + ' working on ' + (n === 1 ? 'its' : 'their') + ' own with nothing changing'
      : held.length + ' session' + (held.length === 1 ? ' is' : 's are') + ' on hold';
    card.innerHTML =
      '<div class="rw-head"><span class="rw-eyebrow"><span class="rw-dot"></span>Runaway</span>' +
      '<button type="button" class="rw-close" data-rw="dismiss-all" title="Dismiss all for 6h" aria-label="Dismiss all">×</button></div>' +
      '<p class="rw-title">' + esc(title) + '</p>' +
      '<ul class="rw-list">' + held.map(function (h) {
        var s = titles[h.session_id] || {};
        var blocked = h.blocked ? h.blocked + ' wake' + (h.blocked === 1 ? '' : 's') + ' blocked' : 'no wakes since';
        return '<li class="rw-row rw-held" data-sid="' + esc(h.session_id) + '">' +
          '<div class="rw-text"><span class="rw-name">' + esc(s.title || h.session_id.slice(0, 8)) + '</span>' +
          '<span class="rw-sub">On hold · ' + esc(blocked) + (h.goal_paused ? ' · goal paused' : '') + '</span></div>' +
          '<div class="rw-actions">' +
          '<button type="button" class="rw-btn rw-primary" data-rw="open">Open</button>' +
          '<button type="button" class="rw-btn" data-rw="release" title="Let it wake itself again">Release</button>' +
          '</div></li>';
      }).join('') + flagged.map(function (s) {
        var engine = s.engine && s.engine !== 'claude' ? s.engine.charAt(0).toUpperCase() + s.engine.slice(1) : '';
        return '<li class="rw-row" data-sid="' + esc(s.session_id) + '">' +
          '<div class="rw-text"><span class="rw-name">' + esc(s.title || s.session_id.slice(0, 8)) + '</span>' +
          '<span class="rw-sub">' + esc([project(s.cwd), engine, since(s.last_human_at)].filter(Boolean).join(' · ')) + '</span>' +
          '<span class="rw-sub">' + esc(summary(s)) + '</span>' +
          (drivers(s.triggers) ? '<span class="rw-sub">Woken by ' + esc(drivers(s.triggers)) + '</span>' : '') + '</div>' +
          '<div class="rw-actions">' +
          '<button type="button" class="rw-btn rw-primary" data-rw="open">Open</button>' +
          '<button type="button" class="rw-btn" data-rw="hold" title="Stop it waking itself (timers, WatchTower, goals) until you release it">Hold</button>' +
          '<button type="button" class="rw-btn" data-rw="lease" title="Hide this session for 24h">Let it run 24h</button>' +
          '<button type="button" class="rw-close" data-rw="dismiss" title="Dismiss for 6h" aria-label="Dismiss">×</button>' +
          '</div></li>';
      }).join('') + '</ul>';
    card.hidden = false;
  }

  function drop(sid) {
    sessions = sessions.filter(function (s) { return s.session_id !== sid; });
    render();
  }

  function onClick(ev) {
    var btn = ev.target.closest('[data-rw]');
    if (!btn) return;
    var action = btn.getAttribute('data-rw');
    if (action === 'dismiss-all') {
      var heldIds = {};
      held.forEach(function (h) { heldIds[h.session_id] = 1; });
      sessions.forEach(function (s) { if (!heldIds[s.session_id]) post('/api/runaway/dismiss', { session_id: s.session_id }); });
      sessions = sessions.filter(function (s) { return heldIds[s.session_id]; });
      held = []; render();
      return;
    }
    var row = btn.closest('.rw-row'), sid = row && row.getAttribute('data-sid');
    if (!sid) return;
    if (action === 'open' && typeof window.cccOpenSession === 'function') window.cccOpenSession(sid);
    else if (action === 'hold') {
      var s = sessions.filter(function (x) { return x.session_id === sid; })[0] || {};
      btn.disabled = true;
      post('/api/runaway/hold', { session_id: sid, engine: s.engine || 'claude' }).then(function (data) {
        if (data && data.ok) { toast('On hold: it will not wake itself until you release it.', 'success'); pollHolds(); }
        else { btn.disabled = false; toast('Could not hold: ' + ((data && data.error) || 'unknown'), 'error'); }
      });
    } else if (action === 'release') {
      btn.disabled = true;
      post('/api/runaway/release', { session_id: sid }).then(function (data) {
        if (data && data.ok) {
          toast('Released: it can wake itself again.', 'success');
          held = held.filter(function (h) { return h.session_id !== sid; });
          sessions = sessions.filter(function (x) { return x.session_id !== sid; });
          render();
        } else { btn.disabled = false; toast('Could not release: ' + ((data && data.error) || 'unknown'), 'error'); }
      });
    } else if (action === 'lease') {
      post('/api/runaway/lease', { session_id: sid, hours: 24 }).then(function (data) {
        if (data && data.ok) toast('Letting it run; it will be flagged again after 24h if still idle.', 'success');
        else toast('Could not save: ' + ((data && data.error) || 'unknown'), 'error');
      });
      drop(sid);
    } else if (action === 'dismiss') { post('/api/runaway/dismiss', { session_id: sid }); drop(sid); }
  }

  function poll() {
    fetch('/api/runaway', { cache: 'no-store' })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (!data || !Array.isArray(data.sessions)) return;
        sessions = data.sessions;
        render();
      })
      .catch(function () {});
  }

  function pollHolds() {
    fetch('/api/runaway/holds', { cache: 'no-store' })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (!data || !Array.isArray(data.held)) return;
        held = data.held;
        render();
      })
      .catch(function () {});
  }

  function start() {
    setTimeout(function () { poll(); pollHolds(); }, 8000);
    setInterval(function () { poll(); pollHolds(); }, POLL_MS);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
