/* Limit-hit failover: the approval card that appears when a session stops on
 * a usage limit. Backed by GET /api/free-failover/status and the
 * /api/free-failover/* action endpoints (ccc_server/free_failover.py).
 *
 * Self-contained by design: the only app.js hook it uses is
 * window.cccOpenSession, and it degrades to nothing on a server old enough
 * to 404 the status endpoint.
 */
(function () {
  'use strict';

  var POLL_MS = 5000;
  var stack = null;
  var lastStatus = null;
  var unsupported = false;
  var backoffUntil = 0;
  var seen = {};           // sid -> signature of the last rendered state
  var busy = {};           // sid -> true while an action request is in flight
  var cardErrors = {};     // sid -> last action error string

  var ENGINE_LABELS = {
    claude: 'Claude', devin: 'Devin', codex: 'Codex', kimi: 'Kimi',
  };

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function ensureStack() {
    if (stack && document.body.contains(stack)) return stack;
    stack = document.getElementById('cccFailoverStack');
    if (!stack) {
      stack = document.createElement('div');
      stack.id = 'cccFailoverStack';
      stack.className = 'ccc-failover-stack';
      stack.setAttribute('aria-live', 'polite');
      document.body.appendChild(stack);
    }
    return stack;
  }

  function engineLabel(engine) {
    return ENGINE_LABELS[engine] || 'This session';
  }

  function sessionTitle(item, sid) {
    var name = item.display_name || item.title;
    if (name) return String(name);
    var raw = String(sid);
    if (raw.indexOf('devincli-') === 0) raw = raw.slice(9);
    return 'Session ' + raw.slice(0, 8);
  }

  function fmtClock(epochS) {
    // "06:22 UTC" — the same shape Devin's limit message uses.
    try {
      var d = new Date(epochS * 1000);
      return d.toISOString().slice(11, 16) + ' UTC';
    } catch (_) {
      return '';
    }
  }

  function fmtIn(epochS, nowS) {
    var left = Math.max(0, Math.round(epochS - nowS));
    if (left < 60) return 'in under a minute';
    var m = Math.floor(left / 60);
    if (m < 60) return 'in ' + m + 'm';
    var h = Math.floor(m / 60);
    m = m % 60;
    return 'in ' + h + 'h' + (m ? ' ' + m + 'm' : '');
  }

  function post(path, payload) {
    return fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
    }).then(function (r) { return r.json().catch(function () { return { ok: false }; }); });
  }

  function act(sid, path, payload) {
    if (busy[sid]) return;
    busy[sid] = true;
    delete cardErrors[sid];
    render();
    post(path, Object.assign({ session_id: sid }, payload))
      .then(function (res) {
        if (!res || !res.ok) {
          cardErrors[sid] = (res && res.error) || 'That did not work. Try again?';
        }
      })
      .catch(function () {
        cardErrors[sid] = 'Could not reach CCC. Try again?';
      })
      .then(function () {
        busy[sid] = false;
        poll(true);
      });
  }

  function openSession(sid) {
    try {
      if (typeof window.cccOpenSession === 'function') window.cccOpenSession(sid);
    } catch (_) {}
  }

  function openSettings() {
    var btn = document.getElementById('settingsBtn');
    if (btn) { try { btn.click(); return; } catch (_) {} }
    var sh = document.getElementById('settingsHubBtn');
    if (sh) { try { sh.click(); } catch (_) {} }
  }

  /* ------------------------------------------------------------------ */

  function cardShell(sid, state) {
    var el = document.createElement('div');
    el.className = 'ccc-failover-card ccc-fo-' + state;
    el.dataset.sid = sid;
    return el;
  }

  function renderLimited(sid, item, now) {
    var el = cardShell(sid, 'limited');
    var eng = engineLabel(item.engine);
    var title = sessionTitle(item, sid);
    var resetBit = '';
    if (item.resume_at) {
      resetBit = 'resets ' + fmtIn(item.resume_at, now)
        + ' (' + fmtClock(item.resume_at) + ')'
        + (item.resume_at_estimated ? ', estimated' : '');
    }
    var canFree = item.supports_continue_free;
    var isDevin = item.engine === 'devin';
    var freeLabel = isDevin
      ? (item.free_model_uid ? 'Continue on ' + item.free_model_uid : 'Continue free')
      : 'Continue on a free model';
    var freeNote = '';
    if (canFree && !isDevin && !lastStatus.free_ready) {
      freeNote = '<div class="ccc-fo-freenote">No free model is set up yet. '
        + '<button type="button" class="ccc-fo-link" data-act="setup">Set one up</button> '
        + 'to unlock the free option.</div>';
    }
    var sub;
    if (isDevin) {
      sub = 'A different free model can pick the chat up right now. Same session, still $0.';
    } else if (canFree) {
      sub = 'Keep working right now on a free model. Same chat, same context. Costs $0.';
    } else {
      sub = 'CCC can wake it up the moment the limit clears. Nothing runs before then.';
    }
    el.innerHTML =
      '<div class="ccc-fo-head">'
      + '  <span class="ccc-fo-icon" aria-hidden="true">⚡</span>'
      + '  <div class="ccc-fo-titleblock">'
      + '    <div class="ccc-fo-title">' + esc(eng) + ' hit its limit</div>'
      + '    <div class="ccc-fo-subtitle"><button type="button" class="ccc-fo-name" data-act="open" title="Open this session">'
      +        esc(title) + '</button>'
      +        (resetBit ? ' paused · ' + esc(resetBit) : ' paused')
      +      '</div>'
      + '  </div>'
      + '  <button type="button" class="ccc-fo-x" data-act="dismiss" aria-label="Dismiss">×</button>'
      + '</div>'
      + '<div class="ccc-fo-body">' + esc(sub) + '</div>'
      + freeNote
      + '<div class="ccc-fo-actions">'
      +   (canFree && (isDevin || lastStatus.free_ready)
      ?     '<button type="button" class="ccc-fo-btn ccc-fo-primary" data-act="continue">' + esc(freeLabel) + '</button>'
      : '')
      +   (item.supports_auto_resume
      ?     '<button type="button" class="ccc-fo-btn ccc-fo-ghost" data-act="arm">Resume at reset</button>'
      : '')
      + '</div>'
      + (canFree && (isDevin || lastStatus.free_ready)
      ?   '<label class="ccc-fo-always"><input type="checkbox" id="cccFoAlways-' + esc(sid) + '"> '
      +     'Always do this for this session</label>'
      : '')
      + errorHtml(sid);
    return el;
  }

  function renderArmed(sid, item, now) {
    var el = cardShell(sid, 'armed');
    var title = sessionTitle(item, sid);
    var when = item.auto_resume_fire_at || item.resume_at;
    var whenTxt = when
      ? fmtClock(when) + ' (' + fmtIn(when, now) + ')'
      : 'when the limit clears';
    el.innerHTML =
      '<div class="ccc-fo-head">'
      + '  <span class="ccc-fo-icon" aria-hidden="true">⏱</span>'
      + '  <div class="ccc-fo-titleblock">'
      + '    <div class="ccc-fo-title">Will resume automatically</div>'
      + '    <div class="ccc-fo-subtitle"><button type="button" class="ccc-fo-name" data-act="open">'
      +        esc(title) + '</button> wakes up ' + esc(whenTxt) + '</div>'
      + '  </div>'
      + '  <button type="button" class="ccc-fo-x" data-act="dismiss-arm" aria-label="Dismiss">×</button>'
      + '</div>'
      + '<div class="ccc-fo-actions">'
      + '  <button type="button" class="ccc-fo-btn ccc-fo-ghost" data-act="disarm">Cancel</button>'
      + '</div>'
      + errorHtml(sid);
    return el;
  }

  function renderFree(sid, item, now) {
    var el = cardShell(sid, 'free');
    var title = sessionTitle(item, sid);
    if (item.state === 'switch_back_pending') {
      el.innerHTML =
        '<div class="ccc-fo-head">'
        + '  <span class="ccc-fo-icon ccc-fo-icon-free" aria-hidden="true">⚡</span>'
        + '  <div class="ccc-fo-titleblock">'
        + '    <div class="ccc-fo-title">Switching back</div>'
        + '    <div class="ccc-fo-subtitle">' + esc(title)
        +      ' moves to your plan when the current step ends</div>'
        + '  </div>'
        + '</div>';
      return el;
    }
    var offers = item.switch_back_offered;
    var head =
      '<div class="ccc-fo-head">'
      + '  <span class="ccc-fo-icon ccc-fo-icon-free" aria-hidden="true">⚡</span>'
      + '  <div class="ccc-fo-titleblock">'
      + '    <div class="ccc-fo-title">'
      +       (offers ? 'Your plan is back' : 'Running on a free model')
      +     '</div>'
      + '    <div class="ccc-fo-subtitle"><button type="button" class="ccc-fo-name" data-act="open">'
      +        esc(title) + '</button>'
      +       (offers ? ' · the limit has reset' : ' · ' + esc(item.free_model || 'free') + ' · $0')
      +     '</div>'
      + '  </div>'
      +   (offers ? '' : '<button type="button" class="ccc-fo-x" data-act="hide-free" aria-label="Hide">×</button>')
      + '</div>';
    var actions = offers
      ? '<div class="ccc-fo-body">Move it back to your plan? It keeps running either way.</div>'
        + '<div class="ccc-fo-actions">'
        + '  <button type="button" class="ccc-fo-btn ccc-fo-primary" data-act="switch-back">Switch back</button>'
        + '  <button type="button" class="ccc-fo-btn ccc-fo-ghost" data-act="keep-free">Keep free</button>'
        + '</div>'
      : '';
    el.innerHTML = head + actions + errorHtml(sid);
    return el;
  }

  function errorHtml(sid) {
    if (!cardErrors[sid]) return '';
    return '<div class="ccc-fo-error" role="alert">' + esc(cardErrors[sid]) + '</div>';
  }

  /* ------------------------------------------------------------------ */

  function render() {
    if (!lastStatus) return;
    var host = ensureStack();
    var sessions = lastStatus.sessions || {};
    var now = Date.now() / 1000;
    var wanted = {};

    Object.keys(sessions).forEach(function (sid) {
      var item = sessions[sid] || {};
      var el = null;
      // Fleet banner (static/fleet-failover.js) owns every sid its grouped
      // endpoint returns; on older servers the global is absent and the
      // per-session cards render as before.
      if (!(window.cccFleetFailover
            && typeof window.cccFleetFailover.covers === 'function'
            && window.cccFleetFailover.covers(sid))) {
      if (item.state === 'free' || item.state === 'switch_back_pending') {
        if (item.state === 'free' && !item.switch_back_offered) {
          // A quiet chip, not a full card: keep the "this is $0" fact visible
          // without nagging. × hides it for THIS failover only — a later
          // free stretch gets a fresh chip (the key carries free_since).
          var hideKey = 'ccc.fo.hide-free.' + sid + '.' + (item.free_since || 0);
          try {
            if (localStorage.getItem(hideKey) === '1') return;
          } catch (_) {}
        }
        el = renderFree(sid, item, now);
      } else if (item.auto_resume_armed) {
        el = renderArmed(sid, item, now);
      } else if (!item.offer_dismissed) {
        el = renderLimited(sid, item, now);
      }
      }
      if (el) wanted[sid] = el;
    });

    // Diff against the DOM: drop cards that vanished, append new ones,
    // replace cards whose rendered state changed. Seen signatures keep a
    // 5s poll from rebuilding a card mid-click.
    Array.prototype.slice.call(host.children).forEach(function (el) {
      var sid = el.dataset.sid;
      if (!wanted[sid]) el.remove();
    });
    Object.keys(wanted).forEach(function (sid) {
      var el = wanted[sid];
      var sig = el.innerHTML.length + '|' + el.className
        + '|' + (sessions[sid].state || '') + '|' + !!busy[sid]
        + '|' + (cardErrors[sid] || '');
      var existing = host.querySelector('[data-sid="' + CSS.escape(sid) + '"]');
      var isNew = !seen[sid];
      if (existing && seen[sid] === sig) return;
      if (existing) existing.replaceWith(el);
      else {
        if (isNew) el.classList.add('ccc-fo-enter');
        host.appendChild(el);
      }
      seen[sid] = sig;
    });
    Object.keys(seen).forEach(function (sid) {
      if (!wanted[sid]) delete seen[sid];
    });
    host.hidden = host.children.length === 0;
  }

  function tickCountdowns() {
    if (!stack || stack.hidden) return;
    // Re-render once a minute-ish cadence is overkill: only the subtitle
    // strings carry relative times, so refresh the whole stack cheaply on
    // a slower tick while a card is up.
    render();
  }

  /* ------------------------------------------------------------------ */

  function poll(force) {
    if (unsupported) return;
    if (!force && document.hidden) return;
    if (!force && Date.now() < backoffUntil) return;
    fetch('/api/free-failover/status', { cache: 'no-store' })
      .then(function (r) {
        if (r.status === 404) { unsupported = true; throw 'unsupported'; }
        if (!r.ok) throw new Error('http ' + r.status);
        return r.json();
      })
      .then(function (data) {
        lastStatus = data;
        backoffUntil = 0;
        render();
      })
      .catch(function (e) {
        if (e === 'unsupported') return;
        backoffUntil = Date.now() + 30000;
      });
  }

  /* ------------------------------------------------------------------ */

  function onClick(ev) {
    var btn = ev.target.closest('[data-act]');
    if (!btn || !stack || !stack.contains(btn)) return;
    var card = btn.closest('.ccc-failover-card');
    if (!card) return;
    var sid = card.dataset.sid;
    var actName = btn.getAttribute('data-act');
    if (actName === 'open') { openSession(sid); return; }
    if (actName === 'setup') { openSettings(); return; }
    if (actName === 'continue') {
      var cb = document.getElementById('cccFoAlways-' + sid);
      act(sid, '/api/free-failover/continue', { always: !!(cb && cb.checked) });
      return;
    }
    if (actName === 'arm') { act(sid, '/api/free-failover/arm', { armed: true }); return; }
    if (actName === 'disarm') { act(sid, '/api/free-failover/arm', { armed: false }); return; }
    if (actName === 'dismiss') {
      act(sid, '/api/free-failover/dismiss', { offer: 'failover' });
      return;
    }
    if (actName === 'dismiss-arm') {
      // Closing an armed card without cancelling would still fire the
      // resume invisibly — disarm first, then dismiss the card.
      post('/api/free-failover/arm', { session_id: sid, armed: false })
        .then(function () { act(sid, '/api/free-failover/dismiss', { offer: 'failover' }); })
        .catch(function () { cardErrors[sid] = 'Could not reach CCC. Try again?'; render(); });
      return;
    }
    if (actName === 'hide-free') {
      var item = (lastStatus.sessions || {})[sid] || {};
      var hideKey = 'ccc.fo.hide-free.' + sid + '.' + (item.free_since || 0);
      try { localStorage.setItem(hideKey, '1'); } catch (_) {}
      render();
      return;
    }
    if (actName === 'switch-back') { act(sid, '/api/free-failover/switch-back', {}); return; }
    if (actName === 'keep-free') {
      act(sid, '/api/free-failover/dismiss', { offer: 'switch_back' });
      return;
    }
  }

  function boot() {
    ensureStack();
    stack.hidden = true;
    stack.addEventListener('click', onClick);
    poll();
    setInterval(poll, POLL_MS);
    // Countdown labels drift on the 5s poll cadence alone; refresh the
    // rendered copy once per 15s while anything is on screen (the data is
    // already local — this only re-runs render()).
    setInterval(tickCountdowns, 15000);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) poll(true);
    });
    // The fleet banner redraws when its grouped payload lands; re-render so
    // covered cards disappear immediately rather than on the next poll.
    document.addEventListener('cccFleetRendered', function () { render(); });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
