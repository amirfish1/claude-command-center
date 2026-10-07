/* Fleet limit view: ONE banner per engine limit wall instead of a card
 * per stopped session. Backed by GET /api/free-failover/fleet and the
 * POST of the same path — a grouped view of the exact state the old
 * per-session cards render, so this module owns the whole lifecycle
 * (stopped -> armed / running free -> switch back) whenever the endpoint
 * answers, and limit-failover.js stays as the fallback for older servers.
 *
 * Self-contained: uses window.cccOpenSession when present, degrades to
 * nothing on a 404. Exposes window.cccFleetFailover = {active, covers}
 * so the per-session module can yield any sid the banner already shows.
 */
(function () {
  'use strict';

  var POLL_MS = 5000;
  var MAX_ROWS_AUTO_EXPANDED = 10;
  var host = null;
  var lastFleet = null;
  var unsupported = false;
  var backoffUntil = 0;
  var checked = {};        // sid -> bool (limited rows; default true)
  var busyGroup = {};      // group key -> action in flight
  var busySid = {};        // sid -> row action in flight
  var errors = {};         // group key -> last batch error text
  var rowErrors = {};      // sid -> last row/batch error text
  var expanded = {};       // group key -> bool
  var coveredSids = {};    // sid -> true, rebuilt each successful poll

  window.cccFleetFailover = {
    active: false,
    covers: function (sid) { return !!coveredSids[sid]; },
  };

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function ensureHost() {
    if (host && document.body.contains(host)) return host;
    host = document.getElementById('cccFleetFailoverHost');
    if (!host) {
      host = document.createElement('div');
      host.id = 'cccFleetFailoverHost';
      host.className = 'ccc-fleet-host';
      host.setAttribute('aria-live', 'polite');
      document.body.appendChild(host);
    }
    return host;
  }

  function sessionTitle(item, sid) {
    var name = item.display_name || item.title;
    if (name) return String(name);
    var raw = String(sid);
    if (raw.indexOf('devincli-') === 0) raw = raw.slice(9);
    return 'Session ' + raw.slice(0, 8);
  }

  function fmtClock(epochS) {
    try {
      var d = new Date(epochS * 1000);
      return d.toISOString().slice(11, 16) + ' UTC';
    } catch (_) { return ''; }
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

  function checkedLimited(group) {
    return group.sessions.filter(function (m) {
      return m.state === 'limited' && checked[m.session_id];
    }).map(function (m) { return m.session_id; });
  }

  function applyBatch(group, action, sids, extra) {
    var key = group.key;
    if (busyGroup[key] || !sids.length) return;
    busyGroup[key] = true;
    delete errors[key];
    render();
    post('/api/free-failover/fleet', Object.assign(
      { action: action, session_ids: sids }, extra || {}
    )).then(function (res) {
      if (!res || typeof res !== 'object') {
        errors[key] = 'Could not reach CCC. Try again?';
        return;
      }
      var failed = 0;
      var firstErr = null;
      (res.results ? Object.keys(res.results) : []).forEach(function (sid) {
        var r = res.results[sid] || {};
        if (r.ok) {
          delete rowErrors[sid];
        } else {
          failed += 1;
          rowErrors[sid] = r.error || 'That did not work. Try again?';
          if (!firstErr) firstErr = rowErrors[sid];
        }
      });
      if (failed && sids.length > 1) {
        errors[key] = failed + ' of ' + sids.length
          + ' could not be moved. ' + (firstErr || 'Try again?');
      } else if (failed) {
        errors[key] = firstErr || 'That did not work. Try again?';
      } else if (res.error) {
        errors[key] = res.error;
      }
    }).catch(function () {
      errors[key] = 'Could not reach CCC. Try again?';
    }).then(function () {
      busyGroup[key] = false;
      poll(true);
    });
  }

  function rowAction(group, sid, action, extra) {
    if (busySid[sid]) return;
    busySid[sid] = true;
    delete rowErrors[sid];
    render();
    post('/api/free-failover/fleet', Object.assign(
      { action: action, session_ids: [sid] }, extra || {}
    )).then(function (res) {
      var r = res && res.results && res.results[sid];
      if (!r || !r.ok) {
        rowErrors[sid] = (r && r.error) || (res && res.error)
          || 'That did not work. Try again?';
      }
    }).catch(function () {
      rowErrors[sid] = 'Could not reach CCC. Try again?';
    }).then(function () {
      busySid[sid] = false;
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

  function rowHtml(group, m, now) {
    var sid = m.session_id;
    var title = esc(sessionTitle(m, sid));
    var meta = '';
    var lead = '';
    var rowActs = '';
    if (m.state === 'limited') {
      lead = '<input type="checkbox" class="ccc-fleet-check"'
        + (checked[sid] ? ' checked' : '') + ' data-sid="' + esc(sid)
        + '" aria-label="Include this session">';
      if (m.resume_at) {
        meta = 'resets ' + esc(fmtIn(m.resume_at, now))
          + ' (' + esc(fmtClock(m.resume_at)) + ')'
          + (m.resume_at_estimated ? ', estimated' : '');
      } else {
        meta = 'paused';
      }
    } else if (m.state === 'armed') {
      lead = '<span class="ccc-fleet-badge ccc-fleet-badge-armed" aria-hidden="true">&#9201;</span>';
      var when = m.auto_resume_fire_at || m.resume_at;
      meta = 'resumes ' + (when
        ? esc(fmtClock(when)) + ' (' + esc(fmtIn(when, now)) + ')'
        : 'when the limit clears');
      rowActs = '<button type="button" class="ccc-fleet-link" data-act="row-disarm" data-sid="'
        + esc(sid) + '">cancel</button>';
    } else if (m.state === 'switch_back_pending') {
      lead = '<span class="ccc-fleet-badge ccc-fleet-badge-free" aria-hidden="true">&#9203;</span>';
      meta = 'moving back to your plan';
    } else { // free
      lead = '<span class="ccc-fleet-badge ccc-fleet-badge-free" aria-hidden="true">&#9889;</span>';
      meta = esc(m.free_model || 'free model') + ' &middot; $0';
      if (m.switch_back_offered) {
        rowActs = '<button type="button" class="ccc-fleet-link" data-act="row-switch-back" data-sid="'
          + esc(sid) + '">switch back</button>'
          + '<button type="button" class="ccc-fleet-link" data-act="row-keep-free" data-sid="'
          + esc(sid) + '">keep free</button>';
      } else {
        var hideKey = 'ccc.fo.hide-free.' + sid + '.' + (m.free_since || 0);
        var hidden = false;
        try { hidden = localStorage.getItem(hideKey) === '1'; } catch (_) {}
        if (hidden) return '';
        rowActs = '<button type="button" class="ccc-fleet-x ccc-fleet-x-row" data-act="row-hide-free" data-sid="'
          + esc(sid) + '" aria-label="Hide">&times;</button>';
      }
    }
    var err = rowErrors[sid]
      ? '<div class="ccc-fleet-rowerr" role="alert">' + esc(rowErrors[sid]) + '</div>' : '';
    return '<div class="ccc-fleet-row ccc-fleet-row-' + esc(m.state)
      + '" data-sid="' + esc(sid) + '">'
      + lead
      + '<div class="ccc-fleet-rowbody">'
      +   '<div class="ccc-fleet-rowname">' + title + '</div>'
      +   '<div class="ccc-fleet-rowmeta">' + meta + '</div>'
      +   err
      + '</div>'
      + rowActs
      + '<button type="button" class="ccc-fleet-open" data-act="open" data-sid="'
      +   esc(sid) + '" aria-label="Open this session">&rsaquo;</button>'
      + '</div>';
  }

  function groupHtml(group, now) {
    var key = group.key;
    var nLimited = group.limited_count;
    var nArmed = group.armed_count;
    var nFree = group.free_count;
    var title;
    if (nLimited || nArmed) {
      title = esc(group.engine_label) + ' limit reached';
    } else {
      title = 'Running free while ' + esc(group.engine_label) + ' rests';
    }
    var parts = [];
    if (nLimited) parts.push(nLimited + ' session' + (nLimited > 1 ? 's' : '') + ' stopped');
    if (nArmed) parts.push(nArmed + ' set to resume at reset');
    if (nFree) parts.push(nFree + ' running free');
    var sub = parts.join(' &middot; ');
    if (nLimited && group.resume_at) {
      sub += ' &middot; resets ' + esc(fmtIn(group.resume_at, now))
        + ' (' + esc(fmtClock(group.resume_at)) + ')'
        + (group.resume_at_estimated ? ', estimated' : '');
    }

    var rows = group.sessions.map(function (m) { return rowHtml(group, m, now); })
      .join('');
    var nRows = group.sessions.length;
    var open = expanded[key];
    if (open == null) open = nRows <= MAX_ROWS_AUTO_EXPANDED;
    var rowsBlock = open
      ? '<div class="ccc-fleet-rows">' + rows + '</div>'
      : '<button type="button" class="ccc-fleet-more" data-act="expand" data-group="'
        + esc(key) + '">Show all ' + nRows + ' sessions</button>';
    if (open && nRows > MAX_ROWS_AUTO_EXPANDED) {
      rowsBlock += '<button type="button" class="ccc-fleet-more" data-act="collapse" data-group="'
        + esc(key) + '">Show fewer</button>';
    }

    var selected = checkedLimited(group);
    var acts = '';
    if (nLimited) {
      var continueBtn = '';
      if (group.can_continue_free) {
        continueBtn = '<button type="button" class="ccc-fleet-btn ccc-fleet-primary" data-act="continue" data-group="'
          + esc(key) + '"' + (selected.length && !busyGroup[key] ? '' : ' disabled') + '>'
          + 'Continue ' + selected.length + ' free</button>';
      } else if (!lastFleet.free_ready && group.engine === 'claude') {
        continueBtn = '<button type="button" class="ccc-fleet-link" data-act="setup">Set up a free model</button>'
          + '<span class="ccc-fleet-note">to keep working for $0</span>';
      }
      var resumeBtn = group.can_auto_resume
        ? '<button type="button" class="ccc-fleet-btn ccc-fleet-ghost" data-act="arm" data-group="'
          + esc(key) + '"' + (selected.length && !busyGroup[key] ? '' : ' disabled') + '>'
          + 'Resume ' + selected.length + ' at reset</button>'
        : '';
      acts = '<div class="ccc-fleet-actions">' + continueBtn + resumeBtn + '</div>';
      if (group.can_continue_free) {
        acts += '<label class="ccc-fleet-always"><input type="checkbox" id="cccFleetAlways-'
          + esc(key) + '"> Do this automatically next time</label>';
      }
    }
    if (group.switch_back_count) {
      var offered = group.sessions.filter(function (m) {
        return m.state === 'free' && m.switch_back_offered;
      }).length;
      acts += '<div class="ccc-fleet-actions">'
        + '<button type="button" class="ccc-fleet-btn ccc-fleet-primary" data-act="switch-back" data-group="'
        +   esc(key) + '">Switch ' + offered + ' back to your plan</button>'
        + '<button type="button" class="ccc-fleet-btn ccc-fleet-ghost" data-act="keep-free" data-group="'
        +   esc(key) + '">Keep them free</button>'
        + '</div>';
    }

    var err = errors[key]
      ? '<div class="ccc-fleet-error" role="alert">' + esc(errors[key]) + '</div>' : '';
    var xBtn = nLimited
      ? '<button type="button" class="ccc-fleet-x" data-act="dismiss" data-group="'
        + esc(key) + '" aria-label="Dismiss">&times;</button>' : '';
    var busy = busyGroup[key] ? ' ccc-fleet-busy' : '';

    return '<div class="ccc-fleet-card ccc-fleet-' + esc(key) + busy + '" data-group="' + esc(key) + '">'
      + '<div class="ccc-fleet-head">'
      +   '<span class="ccc-fleet-icon" aria-hidden="true">&#9889;</span>'
      +   '<div class="ccc-fleet-titleblock">'
      +     '<div class="ccc-fleet-title">' + title + '</div>'
      +     '<div class="ccc-fleet-subtitle">' + sub + '</div>'
      +   '</div>' + xBtn
      + '</div>'
      + rowsBlock + acts + err
      + '</div>';
  }

  function render() {
    if (!lastFleet) return;
    var el = ensureHost();
    var now = Date.now() / 1000;
    var groups = lastFleet.groups || [];
    var html = groups.map(function (g) { return groupHtml(g, now); }).join('');
    // One innerHTML swap keeps 5s polls from stealing focus or toggling a
    // checkbox mid-click; busy flags are rebuilt from state each pass.
    if (el.innerHTML !== html) el.innerHTML = html;
    el.hidden = !groups.length;
    var seen = {};
    groups.forEach(function (g) {
      g.sessions.forEach(function (m) { seen[m.session_id] = true; });
    });
    Object.keys(checked).forEach(function (sid) {
      if (!seen[sid]) delete checked[sid];
    });
    // Nudge the per-session module to re-render: it suppresses any sid this
    // banner covers, and without the nudge a card rendered before the fleet
    // data landed would linger until its next 5s poll.
    try { document.dispatchEvent(new Event('cccFleetRendered')); } catch (_) {}
  }

  /* ------------------------------------------------------------------ */

  function poll(force) {
    if (unsupported) return;
    if (!force && document.hidden) return;
    if (!force && Date.now() < backoffUntil) return;
    fetch('/api/free-failover/fleet', { cache: 'no-store' })
      .then(function (r) {
        if (r.status === 404) { unsupported = true; window.cccFleetFailover.active = false; throw 'unsupported'; }
        if (!r.ok) throw new Error('http ' + r.status);
        return r.json();
      })
      .then(function (data) {
        lastFleet = data;
        backoffUntil = 0;
        window.cccFleetFailover.active = true;
        var sids = {};
        (data.groups || []).forEach(function (g) {
          (g.sessions || []).forEach(function (m) {
            sids[m.session_id] = true;
            if (!(m.session_id in checked) && m.state === 'limited') {
              checked[m.session_id] = true;
            }
          });
        });
        coveredSids = sids;
        render();
      })
      .catch(function (e) {
        if (e === 'unsupported') return;
        backoffUntil = Date.now() + 30000;
      });
  }

  /* ------------------------------------------------------------------ */

  function onClick(ev) {
    var el = ev.target.closest('[data-act]');
    if (!el || !host || !host.contains(el)) return;
    var actName = el.getAttribute('data-act');
    var gkey = el.getAttribute('data-group');
    if (!gkey) {
      var card = el.closest('.ccc-fleet-card');
      gkey = card ? card.getAttribute('data-group') : null;
    }
    var group = null;
    (lastFleet && lastFleet.groups || []).forEach(function (g) {
      if (g.key === gkey) group = g;
    });
    var sid = el.getAttribute('data-sid');

    if (actName === 'open') { openSession(sid); return; }
    if (actName === 'setup') { openSettings(); return; }
    if (actName === 'expand' && group) { expanded[group.key] = true; render(); return; }
    if (actName === 'collapse' && group) { expanded[group.key] = false; render(); return; }
    if (!group) return;

    if (actName === 'continue') {
      var cb = document.getElementById('cccFleetAlways-' + group.key);
      applyBatch(group, 'continue', checkedLimited(group),
        { always: !!(cb && cb.checked) });
      return;
    }
    if (actName === 'arm') {
      applyBatch(group, 'arm', checkedLimited(group));
      return;
    }
    if (actName === 'dismiss') {
      var limited = group.sessions.filter(function (m) {
        return m.state === 'limited';
      }).map(function (m) { return m.session_id; });
      applyBatch(group, 'dismiss', limited, { offer: 'failover' });
      return;
    }
    if (actName === 'switch-back') {
      var offered = group.sessions.filter(function (m) {
        return m.state === 'free' && m.switch_back_offered;
      }).map(function (m) { return m.session_id; });
      applyBatch(group, 'switch_back', offered);
      return;
    }
    if (actName === 'keep-free') {
      var offered2 = group.sessions.filter(function (m) {
        return m.state === 'free' && m.switch_back_offered;
      }).map(function (m) { return m.session_id; });
      applyBatch(group, 'dismiss', offered2, { offer: 'switch_back' });
      return;
    }
    if (actName === 'row-disarm') { rowAction(group, sid, 'disarm'); return; }
    if (actName === 'row-switch-back') { rowAction(group, sid, 'switch_back'); return; }
    if (actName === 'row-keep-free') {
      rowAction(group, sid, 'dismiss', { offer: 'switch_back' });
      return;
    }
    if (actName === 'row-hide-free') {
      var m = null;
      group.sessions.forEach(function (x) { if (x.session_id === sid) m = x; });
      var hideKey = 'ccc.fo.hide-free.' + sid + '.' + ((m && m.free_since) || 0);
      try { localStorage.setItem(hideKey, '1'); } catch (_) {}
      render();
      return;
    }
  }

  function onChange(ev) {
    var cb = ev.target.closest('.ccc-fleet-check');
    if (!cb || !host || !host.contains(cb)) return;
    checked[cb.getAttribute('data-sid')] = !!cb.checked;
    render();
  }

  function boot() {
    ensureHost();
    host.hidden = true;
    host.addEventListener('click', onClick);
    host.addEventListener('change', onChange);
    poll();
    setInterval(poll, POLL_MS);
    // Relative-time labels ("resets in 3h") drift on the poll cadence; the
    // data is already local, so a cheap re-render keeps them honest.
    setInterval(function () { if (host && !host.hidden) render(); }, 15000);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) poll(true);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
