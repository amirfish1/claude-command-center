/* Restart recovery: a machine restart (update, crash, preempted cloud VM)
 * SIGTERMs every agent, and afterwards a session cut off mid-turn looks the
 * same as one that finished. GET /api/restart-recovery lists the ones that
 * were mid-turn when the machine went down (see ccc_server/restart_recovery.py);
 * this card shows them with Open / Resume / Dismiss.
 *
 * Resume sends the session a note (from POST /api/restart-recovery/prompt)
 * through the normal /api/inject-input path, which wakes an idle session.
 * A resumed session drops off the list on its own: its transcript moves.
 *
 * Gate: pop-up id 'restart-interrupted' (static/popups.js).
 */
(function () {
  'use strict';

  var POLL_MS = 5 * 60 * 1000;
  var sessions = [], busy = {}, cssLoaded = false;

  function el(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function post(url, body) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (res) { return res.json().catch(function () { return {}; }); });
  }
  function toast(text, kind) { if (typeof window.showOpToast === 'function') window.showOpToast(text, kind || 'info'); }
  function clock(ts) {
    var d = new Date(ts * 1000);
    var same = d.toDateString() === new Date().toDateString();
    return (same ? '' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ') +
      d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  }
  function stepText(step) {
    step = step || {};
    if (step.kind === 'tool_running') return 'Was running ' + (step.tool || 'a tool') + (step.detail ? ': ' + step.detail : '');
    if (step.kind === 'awaiting_model') return 'A tool had just finished; the model never replied';
    return 'Your last message was never answered';
  }
  function project(cwd) {
    var parts = String(cwd || '').split('/').filter(Boolean);
    return parts.length ? parts[parts.length - 1] : '';
  }

  function render() {
    var card = el('cccRestartRecovery');
    if (!sessions.length || !window.cccPopups || !window.cccPopups.allowed('restart-interrupted')) {
      if (card) card.hidden = true;
      return;
    }
    if (!cssLoaded) {
      var css = document.createElement('link'); css.rel = 'stylesheet'; css.href = '/static/restart-recovery.css'; document.head.appendChild(css);
      cssLoaded = true;
    }
    if (!card) {
      card = document.createElement('section');
      card.id = 'cccRestartRecovery'; card.className = 'rr-card';
      card.setAttribute('role', 'status');
      card.addEventListener('click', onClick);
      document.body.appendChild(card);
    }
    var times = sessions.map(function (s) { return s.shutdown_at; }).filter(Boolean);
    var latest = times.length ? Math.max.apply(null, times) : 0;
    var approx = sessions.some(function (s) { return s.approximate; });
    var n = sessions.length;
    card.innerHTML =
      '<div class="rr-head"><span class="rr-eyebrow"><span class="rr-dot"></span>Restart</span>' +
      '<button type="button" class="rr-close" data-rr="dismiss-all" title="Dismiss all" aria-label="Dismiss all">×</button></div>' +
      '<p class="rr-title">A machine restart cut off ' + n + ' session' + (n === 1 ? '' : 's') + ' mid-turn</p>' +
      (latest ? '<p class="rr-meta">' + (approx ? 'Machine restarted around ' : 'Machine went down at ') + esc(clock(latest)) + '</p>' : '') +
      '<ul class="rr-list">' + sessions.map(function (s) {
        var sid = esc(s.session_id), working = busy[s.session_id];
        return '<li class="rr-row" data-sid="' + sid + '">' +
          '<div class="rr-text"><span class="rr-name">' + esc(s.title || s.session_id.slice(0, 8)) + '</span>' +
          '<span class="rr-sub">' + esc([project(s.cwd), stepText(s.step)].filter(Boolean).join(' · ')) + '</span></div>' +
          '<div class="rr-actions">' +
          '<button type="button" class="rr-btn" data-rr="open">Open</button>' +
          '<button type="button" class="rr-btn rr-primary" data-rr="resume"' + (working ? ' disabled' : '') + '>' + (working ? 'Resuming…' : 'Resume') + '</button>' +
          '<button type="button" class="rr-close" data-rr="dismiss" title="Dismiss" aria-label="Dismiss">×</button>' +
          '</div></li>';
      }).join('') + '</ul>';
    card.hidden = false;
  }

  function drop(sid) {
    sessions = sessions.filter(function (s) { return s.session_id !== sid; });
    render();
  }

  function resume(sid) {
    busy[sid] = true; render();
    post('/api/restart-recovery/prompt', { session_id: sid }).then(function (data) {
      if (!data.ok || !data.text) throw new Error(data.error || 'no resume note');
      return post('/api/inject-input', { session_id: sid, text: data.text });
    }).then(function (data) {
      if (!data.ok) throw new Error(data.error || 'send failed');
      toast('Resumed the session with a restart note.', 'success');
      if (typeof window.cccOpenSession === 'function') window.cccOpenSession(sid);
      drop(sid);
    }).catch(function (err) {
      toast('Resume failed: ' + (err && err.message || 'unknown'), 'error');
    }).finally(function () { delete busy[sid]; render(); });
  }

  function onClick(ev) {
    var btn = ev.target.closest('[data-rr]');
    if (!btn) return;
    var action = btn.getAttribute('data-rr');
    if (action === 'dismiss-all') {
      post('/api/restart-recovery/dismiss', { all: true });
      sessions = []; render();
      return;
    }
    var row = btn.closest('.rr-row'), sid = row && row.getAttribute('data-sid');
    if (!sid) return;
    if (action === 'open' && typeof window.cccOpenSession === 'function') window.cccOpenSession(sid);
    else if (action === 'resume') resume(sid);
    else if (action === 'dismiss') { post('/api/restart-recovery/dismiss', { session_id: sid }); drop(sid); }
  }

  function poll() {
    fetch('/api/restart-recovery', { cache: 'no-store' })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (!data || !Array.isArray(data.sessions)) return;
        sessions = data.sessions;
        render();
      })
      .catch(function () {});
  }

  function start() {
    setTimeout(poll, 3000);
    setInterval(poll, POLL_MS);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
