(function () {
  'use strict';

  var data = null;
  var unsupported = false;
  var fetching = false;
  var retryAt = 0;
  var fetchError = '';
  var selections = Object.create(null);
  var always = Object.create(null);
  var expanded = Object.create(null);
  var busy = Object.create(null);
  var errors = Object.create(null);
  var rowErrors = Object.create(null);
  var covered = Object.create(null);
  var banner;
  var panel;
  var section;

  function esc(value) {
    return String(value == null ? '' : value).replace(/&/g, '&amp;')
      .replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function popupAllowed() {
    return !!(window.cccPopups && window.cccPopups.allowed('fleet-limit'));
  }

  function panelVisible() {
    var modal = document.getElementById('settingsModal');
    return !!(modal && !modal.hidden && section && (
      section.classList.contains('is-active-section') ||
      (modal.querySelector('.is-searching') && !section.hidden)));
  }

  function title(member) {
    return member.display_name || 'Session ' + member.session_id.replace(/^devincli-/, '').slice(0, 8);
  }

  function relative(epoch) {
    var mins = Math.max(0, Math.ceil((epoch - Date.now() / 1000) / 60));
    if (mins < 1) return 'now';
    if (mins < 60) return 'in ' + mins + 'm';
    var hours = Math.floor(mins / 60);
    if (hours < 24) return 'in ' + hours + 'h' + (mins % 60 ? ' ' + mins % 60 + 'm' : '');
    return 'in ' + Math.floor(hours / 24) + 'd ' + hours % 24 + 'h';
  }

  function resetText(member) {
    var epoch = member.auto_resume_fire_at || member.resume_at;
    if (!epoch) return 'Reset time is not known yet';
    return relative(epoch) + ' (' + new Date(epoch * 1000).toLocaleString(undefined, {
      weekday: 'short', hour: 'numeric', minute: '2-digit',
    }) + ')' + (member.resume_at_estimated ? ', estimated' : '');
  }

  function selected(group) {
    return group.sessions.filter(function (m) {
      return m.state === 'limited' && selections[m.session_id] && selections[m.session_id].checked;
    }).map(function (m) { return m.session_id; });
  }

  function button(action, text, sid, disabled) {
    return '<button type="button" class="ccc-fleet-btn" data-act="' + action + '"'
      + (sid ? ' data-sid="' + esc(sid) + '"' : '') + (disabled ? ' disabled' : '') + '>' + esc(text) + '</button>';
  }

  function hiddenFree(member) {
    if (member.state !== 'free' || member.switch_back_offered) return false;
    try { return localStorage.getItem('ccc.fo.hide-free.' + member.session_id + '.' + (member.free_since || 0)) === '1'; }
    catch (_) { return false; }
  }

  function row(group, m, automatic) {
    var sid = m.session_id;
    var disabled = !!busy[group.key];
    var meta;
    var controls = '';
    var check = '';
    if (m.state === 'limited') {
      check = '<input type="checkbox" class="ccc-fleet-check" data-sid="' + esc(sid)
        + '" aria-label="Include ' + esc(title(m)) + '"'
        + (selections[sid].checked ? ' checked' : '') + (disabled ? ' disabled' : '') + '>';
      meta = (m.limit_window === 'weekly' ? 'Weekly limit. Resets ' : 'Resets ') + resetText(m);
    } else if (m.state === 'armed') {
      meta = 'Will resume ' + resetText(m);
      controls = button('disarm', 'Cancel', sid, disabled);
    } else if (m.state === 'switch_back_pending') {
      meta = 'Moving back to your plan after the current step';
    } else {
      meta = (m.free_model || 'Free model') + ' · $0';
      if (m.switch_back_offered) {
        controls = button('switch_back', 'Switch back', sid, disabled)
          + button('keep-free', 'Keep free', sid, disabled);
      } else if (automatic) {
        controls = button('hide-free', 'Hide', sid, disabled);
      }
    }
    return '<div class="ccc-fleet-row" data-row-sid="' + esc(sid) + '">' + check
      + '<div class="ccc-fleet-rowbody"><button type="button" class="ccc-fleet-name" data-act="open" data-sid="'
      + esc(sid) + '">' + esc(title(m)) + '</button><div class="ccc-fleet-meta">' + esc(meta) + '</div>'
      + (rowErrors[sid] ? '<div class="ccc-fleet-error" role="alert">' + esc(rowErrors[sid]) + '</div>' : '')
      + '</div><div class="ccc-fleet-rowactions">' + controls + '</div></div>';
  }

  function groupHtml(group, automatic) {
    var members = group.sessions.filter(function (m) { return !automatic || !hiddenFree(m); });
    if (!members.length) return '';
    var limited = members.filter(function (m) { return m.state === 'limited'; });
    var armed = members.filter(function (m) { return m.state === 'armed'; });
    var free = members.filter(function (m) { return m.state === 'free' || m.state === 'switch_back_pending'; });
    var heading = limited.length || armed.length ? group.engine_label + ' limit reached' : group.engine_label + ' sessions running free';
    var summary = [];
    if (limited.length) summary.push(limited.length + ' session' + (limited.length === 1 ? '' : 's') + ' stopped');
    if (armed.length) summary.push(armed.length + ' scheduled to resume');
    if (free.length) summary.push(free.length + ' running free');
    var disabled = !!busy[group.key];
    var ids = selected(group);
    var all = ids.length === limited.length;
    var actions = '';
    if (limited.length) {
      if (group.can_continue_free) {
        actions += button('continue', 'Continue ' + (all ? 'all ' : '') + ids.length + ' free', null, disabled || !ids.length);
      } else if (group.engine === 'claude') {
        actions += button('setup', 'Set up a free model', null, disabled);
      }
      if (group.can_auto_resume) {
        actions += button('arm', all ? 'Resume all at reset' : 'Resume ' + ids.length + ' at reset', null, disabled || !ids.length);
      }
      if (automatic) actions += button('dismiss', 'Hide notice', null, disabled);
    }
    var offered = free.filter(function (m) { return m.state === 'free' && m.switch_back_offered; });
    if (offered.length) {
      actions += button('switch_back', 'Switch all ' + offered.length + ' back to your plan', null, disabled)
        + button('keep-free', 'Keep them free', null, disabled);
    }
    var open = expanded[group.key] == null ? members.length <= 10 : expanded[group.key];
    var body = open ? '<div class="ccc-fleet-rows">' + members.map(function (m) { return row(group, m, automatic); }).join('') + '</div>' : '';
    if (members.length > 10) body += button('expand', open ? 'Show fewer' : 'Choose sessions (' + members.length + ')', null, disabled);
    var note = '';
    if (limited.length) {
      note = group.can_continue_free ? 'A free model can pick up the same chat.'
        : group.engine === 'claude' ? 'Set up a free model to continue for $0.'
        : 'Free continuation is not available for ' + group.engine_label + '. You can resume at reset.';
      if (group.can_auto_resume) note += ' Resumes are spread out, up to ' + (data.auto_resume_max_per_minute || 5) + ' per minute.';
    }
    return '<section class="ccc-fleet-card" data-group="' + esc(group.key) + '" aria-label="' + esc(heading) + '">'
      + '<h3>' + esc(heading) + '</h3><p class="ccc-fleet-summary">' + esc(summary.join(' · ')) + '</p>'
      + body + (note ? '<p class="ccc-fleet-note">' + esc(note) + '</p>' : '')
      + '<div class="ccc-fleet-actions">' + actions + '</div>'
      + (limited.length && group.can_continue_free ? '<label class="ccc-fleet-always"><input type="checkbox" data-always="'
        + esc(group.key) + '"' + (always[group.key] ? ' checked' : '') + (disabled ? ' disabled' : '')
        + '> Always continue these selected sessions free</label>' : '')
      + (errors[group.key] ? '<p class="ccc-fleet-error" role="alert">' + esc(errors[group.key]) + '</p>' : '')
      + '</section>';
  }

  function swap(host, html) {
    if (!host || host.innerHTML === html) return;
    var active = document.activeElement;
    var focus = active && host.contains(active) ? {
      sid: active.getAttribute('data-sid'), action: active.getAttribute('data-act'),
      always: active.getAttribute('data-always'), group: active.closest('[data-group]').getAttribute('data-group'),
      check: active.classList.contains('ccc-fleet-check'),
    } : null;
    var scrolls = Array.from(host.querySelectorAll('.ccc-fleet-rows')).map(function (el) { return el.scrollTop; });
    host.innerHTML = html;
    Array.from(host.querySelectorAll('.ccc-fleet-rows')).forEach(function (el, i) { el.scrollTop = scrolls[i] || 0; });
    if (focus) {
      var target = Array.from(host.querySelectorAll('button,input')).find(function (el) {
        var card = el.closest('[data-group]');
        return card && card.getAttribute('data-group') === focus.group
          && el.getAttribute('data-sid') === focus.sid && el.getAttribute('data-act') === focus.action
          && el.getAttribute('data-always') === focus.always && el.classList.contains('ccc-fleet-check') === focus.check;
      });
      if (target) target.focus({ preventScroll: true });
    }
  }

  function render() {
    if (!banner || !panel) return;
    var groups = data && data.groups || [];
    var html = groups.map(function (g) { return groupHtml(g, false); }).join('');
    var status = unsupported ? 'Session limits are not available on this server yet.'
      : fetchError || (!data ? 'Checking session limits...' : !groups.length ? 'No sessions are waiting on a limit.' : '');
    swap(panel, (status ? '<p class="ccc-fleet-status" role="status">' + esc(status) + '</p>' : '') + html);
    if (popupAllowed() && !unsupported) {
      swap(banner, groups.map(function (g) { return groupHtml(g, true); }).join(''));
      banner.hidden = !banner.children.length;
    } else {
      banner.hidden = true;
      banner.replaceChildren();
    }
    document.dispatchEvent(new Event('cccFleetRendered'));
  }

  function receive(payload) {
    data = payload;
    var owned = Object.create(null);
    (data.groups || []).forEach(function (g) {
      g.sessions.forEach(function (m) {
        owned[m.session_id] = true;
        if (m.state === 'limited' && (!selections[m.session_id] || selections[m.session_id].stop !== m.detected_at)) {
          selections[m.session_id] = { stop: m.detected_at, checked: true };
          delete rowErrors[m.session_id];
        }
      });
    });
    covered = owned;
    Object.keys(selections).forEach(function (sid) { if (!owned[sid]) delete selections[sid]; });
  }

  function poll(force) {
    if (fetching || unsupported || document.hidden || (!force && !popupAllowed() && !panelVisible()) || Date.now() < retryAt) return;
    fetching = true;
    fetch('/api/free-failover/fleet', { cache: 'no-store' }).then(function (res) {
      if (res.status === 404) {
        unsupported = true;
        covered = Object.create(null);
        data = null;
        return null;
      }
      if (!res.ok) throw new Error('unavailable');
      return res.json();
    }).then(function (payload) {
      if (payload) {
        if (!payload.ok || !Array.isArray(payload.groups)) throw new Error('unavailable');
        receive(payload);
      }
      fetchError = '';
      retryAt = 0;
    }).catch(function () {
      fetchError = 'Could not reach CCC. Retrying shortly.';
      retryAt = Date.now() + 15000;
    }).finally(function () { fetching = false; render(); });
  }

  function act(group, action, ids, extra) {
    if (busy[group.key] || !ids.length) return;
    busy[group.key] = true;
    delete errors[group.key];
    ids.forEach(function (sid) { delete rowErrors[sid]; });
    render();
    fetch('/api/free-failover/fleet', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ action: action, session_ids: ids }, extra || {})),
    }).then(function (res) { return res.json(); }).then(function (res) {
      var failures = 0;
      ids.forEach(function (sid) {
        var result = res.results && res.results[sid];
        if (!result || !result.ok) {
          failures++;
          rowErrors[sid] = result && result.error || res.error || 'Could not change this session. Try again.';
        }
      });
      if (failures) errors[group.key] = (ids.length - failures) + ' of ' + ids.length
        + ' changed. ' + failures + ' need another try. See the session notes.';
    }).catch(function () { errors[group.key] = 'Could not reach CCC. No change was confirmed. Check the sessions before trying again.'; })
      .finally(function () { busy[group.key] = false; retryAt = 0; render(); poll(true); });
  }

  function click(ev) {
    var buttonEl = ev.target.closest('[data-act]');
    if (!buttonEl || buttonEl.disabled) return;
    var card = buttonEl.closest('[data-group]');
    var group = data && data.groups.find(function (g) { return card && g.key === card.getAttribute('data-group'); });
    if (!group) return;
    var action = buttonEl.getAttribute('data-act');
    var sid = buttonEl.getAttribute('data-sid');
    if (action === 'open') { if (typeof window.cccOpenSession === 'function') window.cccOpenSession(sid); return; }
    if (action === 'setup') { var tab = document.getElementById('settingsRailTab-free'); if (tab) tab.click(); return; }
    if (action === 'expand') { expanded[group.key] = !(expanded[group.key] == null ? group.sessions.length <= 10 : expanded[group.key]); render(); return; }
    if (action === 'hide-free') {
      var m = group.sessions.find(function (item) { return item.session_id === sid; });
      try { localStorage.setItem('ccc.fo.hide-free.' + sid + '.' + (m.free_since || 0), '1'); } catch (_) {}
      render(); return;
    }
    var ids = sid ? [sid] : action === 'continue' || action === 'arm' ? selected(group) : group.sessions.filter(function (m) {
      return action === 'dismiss' ? m.state === 'limited' : m.state === 'free' && m.switch_back_offered;
    }).map(function (m) { return m.session_id; });
    var extra = action === 'continue' ? { always: !!always[group.key] } : {};
    if (action === 'keep-free') { action = 'dismiss'; extra.offer = 'switch_back'; }
    act(group, action, ids, extra);
  }

  function change(ev) {
    var input = ev.target;
    if (input.matches('.ccc-fleet-check')) selections[input.getAttribute('data-sid')].checked = input.checked;
    else if (input.hasAttribute('data-always')) always[input.getAttribute('data-always')] = input.checked;
    else return;
    render();
  }

  function boot() {
    var rail = document.getElementById('settingsRail');
    var pane = document.getElementById('settingsPane');
    if (!rail || !pane) return;
    var tab = document.createElement('button');
    tab.type = 'button';
    tab.id = 'settingsRailTab-limits';
    tab.className = 'settings-rail-item';
    tab.setAttribute('data-section-target', 'limits');
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-selected', 'false');
    tab.setAttribute('aria-controls', 'settingsSection-limits');
    tab.innerHTML = '<span>Session limits</span>';
    rail.appendChild(tab);
    section = document.createElement('section');
    section.id = 'settingsSection-limits';
    section.className = 'settings-section';
    section.setAttribute('data-section-id', 'limits');
    section.setAttribute('role', 'tabpanel');
    section.setAttribute('aria-labelledby', tab.id);
    section.setAttribute('aria-hidden', 'true');
    section.innerHTML = '<div class="settings-section-eyebrow">Session limits</div>'
      + '<div class="settings-row" data-keywords="session limits fleet weekly reset free resume paused">'
      + '<div class="settings-row-main"><div class="settings-row-desc">Choose which stopped sessions should continue free or resume when their allowance resets. Nothing starts until you choose.</div></div></div>'
      + '<div id="cccFleetLimitsPanel"></div>';
    pane.appendChild(section);
    panel = document.getElementById('cccFleetLimitsPanel');
    banner = document.createElement('div');
    banner.id = 'cccFleetFailoverHost';
    banner.className = 'ccc-fleet-host';
    banner.hidden = true;
    banner.setAttribute('aria-live', 'polite');
    document.body.appendChild(banner);
    [banner, panel].forEach(function (host) { host.addEventListener('click', click); host.addEventListener('change', change); });
    var observer = new MutationObserver(function () { if (panelVisible()) poll(true); });
    observer.observe(section, { attributes: true, attributeFilter: ['class'] });
    var modal = document.getElementById('settingsModal');
    if (modal) observer.observe(modal, { attributes: true, attributeFilter: ['hidden'] });
    document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });
    window.addEventListener('storage', function () { render(); poll(); });
    render();
    poll(true);
    setInterval(poll, 5000);
  }

  window.cccFleetFailover = {
    covers: function (sid) { return !unsupported && !!covered[sid] && (popupAllowed() || panelVisible()); },
    refresh: function () { return poll(true); },
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
