// Copyright (c) 2026 Amir Fish. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-MIT
//
// App kit: gives a user app (/view/<id>) the standard app shell.
//
//   <script src="/static/app-kit.js" data-app="my-app" defer></script>
//
// It adds two things to the page:
//   1. The app's one builder agent in a resizable pane on the right
//      (started on first use; the session stays loaded).
//      If app.json lists "suggestions", they show above the agent as chips:
//      things you can ask for. Clicking one puts it in the message box.
//   2. A Routines section: one card per routine in app.json with its
//      schedule, last result, an on/off switch and "Run now". Click a card
//      for its last runs, to edit it or delete it. The last card asks the
//      agent to build a new routine.
//
// Mark the page's own content with data-ccc-content (default: <main>) and,
// optionally, where the routine cards go with data-ccc-routines.
// Loaded with #thumb (the Apps home preview) it skips the agent pane.
(function () {
  'use strict';
  var me = document.currentScript;
  var APP = me && me.dataset.app;
  if (!APP) return;
  var thumb = location.hash === '#thumb';
  var $ = function (tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };
  var api = function (path, body) {
    return fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : { cache: 'no-store' })
      .then(function (r) { return r.json().catch(function () { return { ok: false, error: 'HTTP ' + r.status }; }); })
      .catch(function (e) { return { ok: false, error: String(e.message || e) }; });
  };
  var rel = function (t) {
    var s = Math.abs(Date.now() / 1000 - t), f = t > Date.now() / 1000;
    var txt = s < 90 ? 'a minute' : s < 5400 ? Math.round(s / 60) + ' min' : s < 129600 ? Math.round(s / 3600) + ' h' : Math.round(s / 86400) + ' d';
    return f ? 'in ' + txt : txt + ' ago';
  };

  var css = [
    ':root{--k-bg:#0f1115;--k-panel:#161a21;--k-line:#2b323d;--k-text:#edf0f5;--k-muted:#a8b2c2;--k-accent:#80b9ff;--k-good:#8bd8ac;--k-bad:#ff8f8f;--k-tile:#1d232d}',
    '[data-theme="light"]{--k-bg:#f5f6f8;--k-panel:#fff;--k-line:#d9dee6;--k-text:#1a1f29;--k-muted:#5d6878;--k-accent:#1f6fd0;--k-good:#1c8a50;--k-bad:#c62828;--k-tile:#eef1f5}',
    '.ccck-shell{display:grid;grid-template-columns:minmax(0,1fr) 5px var(--ccck-w,36%);height:100vh;color:var(--k-text)}',
    '.ccck-content{min-width:0;overflow:auto;height:100vh}',
    '.ccck-grip{cursor:col-resize;background:var(--k-line);touch-action:none}.ccck-grip:hover,body.ccck-drag .ccck-grip{background:var(--k-accent)}',
    'body.ccck-drag iframe{pointer-events:none}body.ccck-drag{user-select:none;cursor:col-resize}',
    '.ccck-side{min-width:0;display:flex;flex-direction:column;background:var(--k-panel);height:100vh}',
    '.ccck-bar{display:flex;align-items:center;gap:8px;padding:8px 12px;border-bottom:1px solid var(--k-line);font:600 13px/1.3 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}',
    '.ccck-bar .sp{flex:1}',
    '.ccck-btn{font:inherit;font-weight:500;font-size:13px;color:var(--k-text);background:none;border:1px solid var(--k-line);border-radius:8px;padding:5px 11px;cursor:pointer}',
    '.ccck-btn:hover{border-color:var(--k-accent)}.ccck-btn:disabled{opacity:.55;cursor:default}',
    '.ccck-btn.pri{background:var(--k-accent);border-color:var(--k-accent);color:var(--k-bg);font-weight:600}',
    '.ccck-frames{flex:1;min-height:0;position:relative}',
    '.ccck-frames iframe{position:absolute;inset:0;width:100%;height:100%;border:0}.ccck-frames iframe[hidden]{display:none}',
    '.ccck-hero{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:24px;text-align:center;color:var(--k-muted);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--k-panel)}',
    '.ccck-hero .big{font-size:46px}.ccck-hero[hidden]{display:none}',
    '.ccck-tips{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:8px 12px;border-bottom:1px solid var(--k-line);font:13px/1.3 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--k-muted)}',
    '.ccck-tip{font:inherit;color:var(--k-text);background:var(--k-tile);border:1px solid var(--k-line);border-radius:999px;padding:4px 11px;cursor:pointer}.ccck-tip:hover{border-color:var(--k-accent)}',
    '.ccck-modes{display:none}',
    '@media(max-width:900px){.ccck-shell{grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr)}.ccck-modes{display:flex;gap:6px;padding:8px 12px;border-bottom:1px solid var(--k-line);background:var(--k-panel)}.ccck-grip{display:none}.ccck-content,.ccck-side{height:auto;min-height:0}.ccck-shell[data-mode="app"] .ccck-side,.ccck-shell[data-mode="agent"] .ccck-content{display:none}}',
    '.ccck-modes .ccck-btn[aria-pressed="true"]{border-color:var(--k-accent)}',
    '.ccck-routines{font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--k-text)}',
    '.ccck-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}',
    '.ccck-r{display:flex;gap:12px;background:var(--k-panel);border:1px solid var(--k-line);border-radius:14px;padding:14px}',
    '.ccck-r.alert{border-color:var(--k-bad);box-shadow:0 0 0 1px var(--k-bad)}.ccck-r.off{opacity:.6}',
    '.ccck-ico{flex:none;width:52px;height:52px;border-radius:50%;background:var(--k-tile);display:grid;place-items:center;font-size:26px}',
    '.ccck-r.running .ccck-ico{animation:ccck-bob 1s ease-in-out infinite}@keyframes ccck-bob{50%{transform:translateY(-4px)}}',
    '@media(prefers-reduced-motion:reduce){.ccck-r.running .ccck-ico{animation:none}}',
    '.ccck-main{flex:1;min-width:0}.ccck-name{font-weight:650;font-size:15px}',
    '.ccck-does,.ccck-when{color:var(--k-muted);font-size:13px}',
    '.ccck-say{margin:8px 0 4px;padding:7px 10px;border-radius:10px;background:var(--k-tile);font-size:13px}',
    '.ccck-r.alert .ccck-say{color:var(--k-bad);font-weight:600}.ccck-r.failed .ccck-say{color:var(--k-bad)}',
    '.ccck-acts{display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap}',
    '.ccck-sw{position:relative;width:38px;height:22px;border-radius:11px;border:0;background:var(--k-line);cursor:pointer;padding:0;flex:none}',
    '.ccck-sw::after{content:"";position:absolute;top:3px;left:3px;width:16px;height:16px;border-radius:50%;background:#fff;transition:left .15s}',
    '.ccck-sw[aria-checked="true"]{background:var(--k-good)}.ccck-sw[aria-checked="true"]::after{left:19px}',
    '.ccck-new{border-style:dashed;flex-direction:column;gap:8px}',
    '.ccck-r{transition:border-color .15s,transform .15s}.ccck-r:not(.ccck-new):hover{border-color:var(--k-accent);transform:translateY(-1px)}',
    '.ccck-ico,.ccck-head{cursor:pointer}.ccck-chev{margin-left:6px;color:var(--k-muted);font-size:11px}',
    '.ccck-r.open{grid-column:1/-1}',
    '.ccck-more{margin-top:12px;border-top:1px solid var(--k-line);padding-top:10px}',
    '.ccck-sub,.ccck-lab{font-size:12px;font-weight:600;color:var(--k-muted);text-transform:uppercase;letter-spacing:.04em;margin:8px 0 4px}',
    '.ccck-run{display:flex;gap:10px;align-items:baseline;font-size:13px;padding:3px 0}.ccck-rwhen{flex:none;width:76px;color:var(--k-muted)}.ccck-rsum{min-width:0;overflow-wrap:anywhere}',
    '.ccck-dot{flex:none;width:9px;height:9px;border-radius:50%;background:var(--k-muted);align-self:center}.ccck-dot.good{background:var(--k-good)}.ccck-dot.bad{background:var(--k-bad)}.ccck-dot.warn{background:#e0a030}',
    '.ccck-btn.danger{color:var(--k-bad)}.ccck-btn.danger:hover{border-color:var(--k-bad)}',
    '.ccck-edit label{display:block;font-size:12px;font-weight:600;color:var(--k-muted);text-transform:uppercase;letter-spacing:.04em}',
    '.ccck-edit input,.ccck-edit select{font:inherit;font-size:14px;color:var(--k-text);background:var(--k-bg);border:1px solid var(--k-line);border-radius:8px;padding:6px 9px}',
    '.ccck-edit label input{display:block;width:100%;box-sizing:border-box;margin-top:4px;font-weight:400;text-transform:none;letter-spacing:0}',
    '.ccck-icons{display:flex;flex-wrap:wrap;gap:6px;align-items:center}.ccck-icons input{width:64px;text-align:center}',
    '.ccck-ic{font-size:20px;width:38px;height:38px;border-radius:10px;border:1px solid var(--k-line);background:var(--k-tile);cursor:pointer;padding:0}.ccck-ic:hover,.ccck-ic[aria-pressed="true"]{border-color:var(--k-accent);box-shadow:0 0 0 1px var(--k-accent)}',
    '.ccck-when-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.ccck-when-row input[type=number]{width:72px}[hidden]{display:none!important}',
    '.ccck-new textarea{width:100%;min-height:54px;resize:vertical;font:inherit;color:var(--k-text);background:var(--k-bg);border:1px solid var(--k-line);border-radius:10px;padding:8px 10px}',
    '.ccck-note{color:var(--k-muted);font-size:13px;margin-top:8px}'
  ].join('\n');
  document.head.appendChild($('style', null, css));

  var state = null, agentId = '', shell = null, frames = null, hero = null, barName = null, backBtn = null;
  var frameFor = {}, tips = null, tipsDrawn = '', lastFilled = '';

  // "Try asking" chips: what this app's agent can do, from app.json.
  function drawTips() {
    if (!tips || !state) return;
    var list = state.suggestions || [];
    var html = list.length ? '<span>Try asking:</span>' + list.map(function (x, i) {
      return '<button type="button" class="ccck-tip" data-tip="' + i + '" title="' + esc(x.prompt) + '">' + esc(x.label) + '</button>';
    }).join('') : '';
    if (html === tipsDrawn) return;
    tipsDrawn = html; tips.innerHTML = html; tips.hidden = !html;
  }
  // Put a suggestion in the agent's message box for the user to finish and
  // send. The session view is a same-origin frame that may still be loading.
  function fillComposer(text) {
    (agentId ? Promise.resolve(agentId) : startAgent(false)).then(function (sid) {
      if (!sid) return;
      showSession(sid, 'Agent'); setMode('agent');
      var f = frameFor[sid], tries = 0;
      (function put() {
        var box = null;
        try { box = f.contentDocument.getElementById('convInput'); } catch (_) {}
        if (box && box.offsetParent !== null) {
          var mine = !box.value.trim() || box.value === lastFilled;
          box.value = lastFilled = mine ? text : box.value.replace(/\s+$/, '') + '\n' + text;
          box.dispatchEvent(new f.contentWindow.Event('input', { bubbles: true }));
          box.focus();
          try { box.setSelectionRange(box.value.length, box.value.length); } catch (_) {}
          return;
        }
        if (++tries < 60) setTimeout(put, 500);
      })();
    });
  }

  // ---- agent pane -------------------------------------------------------
  function showSession(id, label) {
    if (!frames) return;
    var f = frameFor[id];
    if (!f) {
      f = frameFor[id] = $('iframe');
      f.title = 'Agent session';
      f.src = '/?ccc_popout=conversation&conv=' + encodeURIComponent(id);
      frames.appendChild(f);
    }
    Object.keys(frameFor).forEach(function (k) { frameFor[k].hidden = k !== id; });
    hero.hidden = true;
    barName.textContent = label || 'Agent';
    backBtn.hidden = id === agentId || !agentId;
  }
  function showHero(html) { hero.hidden = false; hero.innerHTML = html; }
  function noAgent() {
    showHero('<div class="big">🤖</div><div><b>This app has no agent yet.</b><br>The agent builds and changes the app for you.</div><button type="button" class="ccck-btn pri" data-k="start">Start the agent</button>');
  }
  function startAgent(fresh) {
    showHero('<div class="big">🤖</div><div>Starting the agent…</div>');
    return api('/api/apps/agent', { app: APP, fresh: !!fresh }).then(function (r) {
      if (!r.ok) { showHero('<div class="big">🤖</div><div>Could not start the agent: ' + esc(r.error) + '</div><button type="button" class="ccck-btn" data-k="start">Try again</button>'); return ''; }
      agentId = r.session_id; showSession(agentId, 'Agent'); return agentId;
    });
  }
  function setMode(m) {
    shell.dataset.mode = m;
    shell.querySelectorAll('.ccck-modes .ccck-btn').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.mode === m)); });
  }
  function buildShell(content) {
    shell = $('div', 'ccck-shell');
    shell.dataset.mode = 'app';
    content.parentNode.insertBefore(shell, content);
    var modes = $('nav', 'ccck-modes', '<button type="button" class="ccck-btn" data-mode="app" aria-pressed="true">App</button><button type="button" class="ccck-btn" data-mode="agent" aria-pressed="false">Agent</button>');
    var grip = $('div', 'ccck-grip'); grip.setAttribute('role', 'separator'); grip.setAttribute('aria-orientation', 'vertical'); grip.title = 'Drag to resize';
    var side = $('aside', 'ccck-side', '<div class="ccck-bar"><span data-k="name">Agent</span><span class="sp"></span><button type="button" class="ccck-btn" data-k="back" hidden>Back to the app’s agent</button><button type="button" class="ccck-btn" data-k="fresh" title="Start a new agent for this app">New agent</button></div><div class="ccck-tips" hidden></div><div class="ccck-frames"><div class="ccck-hero"></div></div>');
    content.classList.add('ccck-content');
    shell.appendChild(modes); shell.appendChild(content); shell.appendChild(grip); shell.appendChild(side);
    frames = side.querySelector('.ccck-frames'); hero = side.querySelector('.ccck-hero');
    tips = side.querySelector('.ccck-tips');
    tips.addEventListener('click', function (e) {
      var b = e.target.closest('[data-tip]');
      var sug = b && state && (state.suggestions || [])[+b.dataset.tip];
      if (sug) fillComposer(sug.prompt);
    });
    barName = side.querySelector('[data-k="name"]'); backBtn = side.querySelector('[data-k="back"]');
    showHero('<div>Opening the agent…</div>');
    modes.addEventListener('click', function (e) { var b = e.target.closest('[data-mode]'); if (b) setMode(b.dataset.mode); });
    side.addEventListener('click', function (e) {
      var k = e.target.closest('[data-k]'); k = k && k.dataset.k;
      if (k === 'start') startAgent(false);
      else if (k === 'back') showSession(agentId, 'Agent');
      else if (k === 'fresh' && confirm('Start a new agent for this app? The current one stays in your sessions.')) startAgent(true);
    });
    // Drag to resize; the width is remembered on this browser.
    var key = 'ccc-app-side-w';
    var set = function (w) { w = Math.max(320, Math.min(shell.clientWidth * 0.75, w)); shell.style.setProperty('--ccck-w', w + 'px'); return w; };
    try { var saved = +localStorage.getItem(key); if (saved) set(saved); } catch (_) {}
    grip.addEventListener('pointerdown', function (e) {
      e.preventDefault(); grip.setPointerCapture(e.pointerId); document.body.classList.add('ccck-drag');
      var move = function (ev) { var w = set(shell.getBoundingClientRect().right - ev.clientX); try { localStorage.setItem(key, String(Math.round(w))); } catch (_) {} };
      var up = function () { document.body.classList.remove('ccck-drag'); grip.removeEventListener('pointermove', move); grip.removeEventListener('pointerup', up); grip.removeEventListener('pointercancel', up); };
      grip.addEventListener('pointermove', move); grip.addEventListener('pointerup', up); grip.addEventListener('pointercancel', up);
    });
  }

  // ---- routines ----------------------------------------------------------
  var list = null, addCard = null, lastDrawn = '';
  var openIds = {}, editId = '', editSched = null;
  var ICONS = ['⏰', '🛎', '🔎', '📬', '☀️', '🌙', '💸', '🧳', '⛷️', '🌦', '📈', '🧹', '🔔', '🤖', '🎯', '✨'];
  var UNITS = { m: 'minutes', h: 'hours', d: 'days' };
  // 'daily 08:00' / 'every 6h' / '' -> the picker's model.
  function schedModel(t) {
    var m = /^daily\s+(\d{1,2}):(\d{2})$/i.exec(t || '');
    if (m) return { mode: 'daily', time: ('0' + m[1]).slice(-2) + ':' + m[2], n: 6, unit: 'h' };
    m = /^every\s+(\d+)\s*(m|min|h|d)$/i.exec(t || '');
    if (m) return { mode: 'every', time: '08:00', n: +m[1], unit: m[2] === 'min' ? 'm' : m[2].toLowerCase() };
    return { mode: t ? 'every' : 'none', time: '08:00', n: 6, unit: 'h' };
  }
  function schedString(m) {
    if (m.mode === 'none') return '';
    if (m.mode === 'daily') return 'daily ' + m.time;
    return 'every ' + Math.max(1, m.n | 0) + m.unit;
  }
  function history(r) {
    var h = (r.history || []).slice(0, 10);
    if (!h.length) return '<div class="ccck-hist"><div class="ccck-note">No runs yet. Press Run now to try it.</div></div>';
    return '<div class="ccck-hist"><div class="ccck-sub">Last ' + h.length + ' run' + (h.length > 1 ? 's' : '') + '</div>' + h.map(function (x) {
      var bad = x.status === 'failed';
      return '<div class="ccck-run"><span class="ccck-dot ' + (bad ? 'bad' : x.alert ? 'warn' : 'good') + '" title="' + (bad ? 'Failed' : 'Worked') + '"></span><span class="ccck-rwhen">' + esc(rel(x.at)) + '</span><span class="ccck-rsum">' + esc(x.summary) + '</span></div>';
    }).join('') + '</div>';
  }
  function editor(r) {
    var m = editSched || schedModel(r.schedule);
    var opts = ICONS.map(function (i) { return '<button type="button" class="ccck-ic" data-ico="' + i + '" aria-pressed="' + (i === r.icon) + '">' + i + '</button>'; }).join('');
    var sel = function (v, label) { return '<option value="' + v + '"' + (m.mode === v ? ' selected' : '') + '>' + label + '</option>'; };
    var unit = function (v) { return '<option value="' + v + '"' + (m.unit === v ? ' selected' : '') + '>' + UNITS[v] + '</option>'; };
    return '<div class="ccck-edit"><label>Name<input data-f="name" maxlength="80" value="' + esc(r.name) + '"></label>' +
      '<div class="ccck-lab">Icon</div><div class="ccck-icons">' + opts + '<input data-f="icon" maxlength="4" value="' + esc(r.icon) + '" aria-label="Or type your own icon"></div>' +
      '<div class="ccck-lab">When</div><div class="ccck-when-row"><select data-f="mode" aria-label="How often">' + sel('daily', 'Every day at') + sel('every', 'Every') + sel('none', 'Only when I press Run now') + '</select>' +
      '<input type="time" data-f="time" value="' + esc(m.time) + '"' + (m.mode === 'daily' ? '' : ' hidden') + '>' +
      '<input type="number" min="1" max="999" data-f="n" value="' + m.n + '"' + (m.mode === 'every' ? '' : ' hidden') + '>' +
      '<select data-f="unit"' + (m.mode === 'every' ? '' : ' hidden') + '>' + unit('m') + unit('h') + unit('d') + '</select></div>' +
      '<div class="ccck-note" data-f="err" hidden></div>' +
      '<div class="ccck-acts"><button type="button" class="ccck-btn pri" data-k="save">Save</button><button type="button" class="ccck-btn" data-k="cancel">Cancel</button></div></div>';
  }
  function card(r) {
    var open = !!openIds[r.id] && !thumb, editing = editId === r.id && open;
    var cls = 'ccck-r' + (r.alert ? ' alert' : '') + (r.enabled ? '' : ' off') + (r.running ? ' running' : '') + (r.status === 'failed' ? ' failed' : '') + (open ? ' open' : '');
    var say = r.running ? (r.kind === 'agent' ? 'An agent is on it…' : 'Running…')
      : r.status === 'never' ? 'Has not run yet.'
      : (r.status === 'failed' ? 'Failed: ' : '') + r.summary;
    var when = [r.schedule_text];
    if (r.last_run && !r.running) when.push('last ran ' + rel(r.last_run));
    if (!r.enabled) when.push('switched off');
    else if (r.next_run && !r.running) when.push(r.next_run <= Date.now() / 1000 ? 'due now' : 'next ' + rel(r.next_run));
    var more = '';
    if (open) {
      more = '<div class="ccck-more">' + (editing ? editor(r) : history(r) +
        '<div class="ccck-acts"><button type="button" class="ccck-btn" data-k="edit">Edit</button><button type="button" class="ccck-btn danger" data-k="delete">Delete</button></div>') + '</div>';
    }
    return '<div class="' + cls + '" data-id="' + esc(r.id) + '"><div class="ccck-ico" data-k="expand" title="Show history">' + esc(r.icon) + '</div><div class="ccck-main">' +
      '<div class="ccck-head" data-k="expand" role="button" tabindex="0" aria-expanded="' + open + '"><div class="ccck-name">' + esc(r.name) + '<span class="ccck-chev">' + (open ? '▴' : '▾') + '</span></div><div class="ccck-does">' + esc(r.does) + '</div></div>' +
      '<div class="ccck-say">' + esc(say) + '</div><div class="ccck-when">' + esc(when.join(' · ')) + '</div>' +
      '<div class="ccck-acts"><button type="button" class="ccck-sw" role="switch" aria-checked="' + r.enabled + '" aria-label="' + esc(r.name) + ' on or off" data-k="toggle"></button>' +
      '<button type="button" class="ccck-btn" data-k="run"' + (r.running ? ' disabled' : '') + '>Run now</button>' +
      (r.kind === 'agent' && r.session_id && !thumb ? '<button type="button" class="ccck-btn" data-k="open">See its work</button>' : '') +
      '</div>' + more + '</div></div>';
  }
  function draw(force) {
    if (!list || !state) return;
    var html = state.routines.map(card).join('');
    if (html === lastDrawn && !force) return;
    // Leave an open editor alone so a background refresh never eats typing.
    if (editId && list.querySelector('.ccck-edit') && !force) return;
    lastDrawn = html;
    Array.prototype.slice.call(list.children).forEach(function (c) { if (c !== addCard) list.removeChild(c); });
    var t = $('div', null, html);
    while (t.firstChild) list.insertBefore(t.firstChild, addCard);
  }
  // Tell the page when its own files changed (the agent edited data, a
  // routine wrote a result), so it can redraw without a refresh button:
  //   window.addEventListener('ccc:app-changed', e => e.detail.page ? ... : ...)
  var lastStamp = null;
  function noteStamp(s) {
    if (!s || typeof s !== 'object') return;
    var was = lastStamp; lastStamp = s;
    if (!was) return;
    var page = s.page !== was.page, files = s.files !== was.files;
    if (page || files) window.dispatchEvent(new CustomEvent('ccc:app-changed', { detail: { page: page, files: files } }));
  }
  function refresh() {
    return api('/api/apps/routines?app=' + encodeURIComponent(APP)).then(function (r) {
      if (!r.ok) return;
      state = r; draw(); drawTips(); noteStamp(r.stamp);
      var id = (r.agent && r.agent.session_id) || '';
      if (shell && id !== agentId) { agentId = id; if (id) showSession(id, 'Agent'); }
      else if (shell && !id && !hero.dataset.asked) { hero.dataset.asked = '1'; noAgent(); }
    });
  }
  function readEditor(c) {
    var g = function (f) { return c.querySelector('[data-f="' + f + '"]'); };
    var m = { mode: g('mode').value, time: g('time').value || '08:00', n: +g('n').value || 1, unit: g('unit').value };
    return { name: g('name').value.trim(), icon: g('icon').value.trim(), model: m };
  }
  function showEditorMode(c) {
    var e = readEditor(c), g = function (f) { return c.querySelector('[data-f="' + f + '"]'); };
    g('time').hidden = e.model.mode !== 'daily'; g('n').hidden = g('unit').hidden = e.model.mode !== 'every';
    editSched = e.model;
  }
  function saveEdit(c, id, b) {
    var e = readEditor(c), err = c.querySelector('[data-f="err"]');
    var fail = function (t) { err.hidden = false; err.textContent = t; err.style.color = 'var(--k-bad)'; };
    if (!e.name) return fail('Give it a name.');
    if (!e.icon) return fail('Pick an icon.');
    b.disabled = true;
    api('/api/apps/routines/update', { app: APP, id: id, name: e.name, icon: e.icon, schedule: schedString(e.model) }).then(function (r) {
      if (!r.ok) { b.disabled = false; return fail(r.error || 'Could not save.'); }
      editId = ''; editSched = null;
      return refresh().then(function () { draw(true); });
    });
  }
  function buildRoutines(content) {
    var host = content.querySelector('[data-ccc-routines]');
    if (!host) { host = $('section'); host.innerHTML = '<h2>Routines</h2>'; content.appendChild(host); }
    var box = $('div', 'ccck-routines');
    list = $('div', 'ccck-list');
    box.appendChild(list);
    host.appendChild(box);
    addCard = $('div', 'ccck-r ccck-new', '<div class="ccck-name">＋ New routine</div><textarea placeholder="What should it do, and how often? For example: every morning, tell me if hotel prices dropped." aria-label="Describe the new routine"></textarea><div class="ccck-acts"><button type="button" class="ccck-btn pri">Ask the agent to build it</button></div><div class="ccck-note" hidden></div>');
    if (!thumb) list.appendChild(addCard);
    function toggleOpen(id) {
      if (openIds[id]) { delete openIds[id]; if (editId === id) { editId = ''; editSched = null; } } else openIds[id] = 1;
      draw(true);
    }
    list.addEventListener('keydown', function (e) {
      var h = e.target.closest('.ccck-head');
      if (h && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); toggleOpen(h.closest('.ccck-r').dataset.id); }
    });
    list.addEventListener('change', function (e) {
      var c = e.target.closest('.ccck-edit');
      if (c && e.target.matches('[data-f="mode"],[data-f="unit"],[data-f="n"],[data-f="time"]')) showEditorMode(c);
    });
    list.addEventListener('click', function (e) {
      var ico = e.target.closest('[data-ico]');
      if (ico) {
        var ed = ico.closest('.ccck-edit');
        ed.querySelector('[data-f="icon"]').value = ico.dataset.ico;
        ed.querySelectorAll('[data-ico]').forEach(function (x) { x.setAttribute('aria-pressed', String(x === ico)); });
        return;
      }
      var b = e.target.closest('[data-k]'), c = e.target.closest('.ccck-r');
      if (!b || !c || c === addCard) return;
      var id = c.dataset.id, r = state.routines.filter(function (x) { return x.id === id; })[0];
      if (!r) return;
      var k = b.dataset.k;
      if (k === 'expand') { if (!thumb) toggleOpen(id); }
      else if (k === 'run') { b.disabled = true; api('/api/apps/routines/run', { app: APP, id: id }).then(function () { setTimeout(refresh, 400); setTimeout(refresh, 2500); }); }
      else if (k === 'toggle') api('/api/apps/routines/toggle', { app: APP, id: id, enabled: !r.enabled }).then(refresh);
      else if (k === 'open' && shell) { showSession(r.session_id, r.name); setMode('agent'); }
      else if (k === 'edit') { editId = id; editSched = null; draw(true); var f = list.querySelector('.ccck-edit [data-f="name"]'); if (f) f.focus(); }
      else if (k === 'cancel') { editId = ''; editSched = null; draw(true); }
      else if (k === 'save') saveEdit(c.querySelector('.ccck-edit'), id, b);
      else if (k === 'delete' && confirm('Delete "' + r.name + '"? It stops running and its history is erased.')) {
        api('/api/apps/routines/delete', { app: APP, id: id }).then(function (res) {
          if (!res.ok) { alert(res.error || 'Could not delete.'); return; }
          delete openIds[id]; editId = '';
          return refresh().then(function () { draw(true); });
        });
      }
    });
    if (thumb) return;
    var ta = addCard.querySelector('textarea'), go = addCard.querySelector('button'), note = addCard.querySelector('.ccck-note');
    go.addEventListener('click', function () {
      var want = ta.value.trim();
      if (!want) { ta.focus(); return; }
      go.disabled = true; note.hidden = false; note.textContent = 'Asking the agent…';
      (agentId ? Promise.resolve(agentId) : startAgent(false)).then(function (sid) {
        if (!sid) throw new Error('no agent');
        return api('/api/inject-input', { session_id: sid, text: 'Add a new routine to this app: "' + want + '". Use a script routine when a script is enough, and an agent routine only when it needs judgement or looking things up. Give it a short friendly name and an icon, follow the Routines section of the applications doc, test it once, then tell me in two lines what you built and when it first runs.' });
      }).then(function (r) {
        if (r && r.ok === false) throw new Error(r.error || 'send failed');
        ta.value = ''; note.textContent = 'Sent. The agent is building it; the new card appears here when it is ready.';
        if (shell) { showSession(agentId, 'Agent'); setMode('agent'); }
      }).catch(function (e) { note.textContent = 'Could not reach the agent: ' + (e.message || e); })
        .then(function () { go.disabled = false; });
    });
  }

  function boot() {
    var content = document.querySelector('[data-ccc-content]') || document.querySelector('main');
    if (!content) return;
    if (!thumb) buildShell(content);
    buildRoutines(content);
    refresh();
    // Keep the cards current without a refresh button.
    setInterval(function () { if (!document.hidden) refresh(); }, 10000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) refresh(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
