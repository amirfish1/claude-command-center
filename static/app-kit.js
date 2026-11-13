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
//   2. A Routines section: one card per routine in app.json with its
//      schedule, last result, an on/off switch and "Run now", plus a box
//      that asks the agent to build a new routine.
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
    '.ccck-new textarea{width:100%;min-height:54px;resize:vertical;font:inherit;color:var(--k-text);background:var(--k-bg);border:1px solid var(--k-line);border-radius:10px;padding:8px 10px}',
    '.ccck-note{color:var(--k-muted);font-size:13px;margin-top:8px}'
  ].join('\n');
  document.head.appendChild($('style', null, css));

  var state = null, agentId = '', shell = null, frames = null, hero = null, barName = null, backBtn = null;
  var frameFor = {};

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
    var side = $('aside', 'ccck-side', '<div class="ccck-bar"><span data-k="name">Agent</span><span class="sp"></span><button type="button" class="ccck-btn" data-k="back" hidden>Back to the app’s agent</button><button type="button" class="ccck-btn" data-k="fresh" title="Start a new agent for this app">New agent</button></div><div class="ccck-frames"><div class="ccck-hero"></div></div>');
    content.classList.add('ccck-content');
    shell.appendChild(modes); shell.appendChild(content); shell.appendChild(grip); shell.appendChild(side);
    frames = side.querySelector('.ccck-frames'); hero = side.querySelector('.ccck-hero');
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
  var list = null, lastDrawn = '';
  function card(r) {
    var cls = 'ccck-r' + (r.alert ? ' alert' : '') + (r.enabled ? '' : ' off') + (r.running ? ' running' : '') + (r.status === 'failed' ? ' failed' : '');
    var say = r.running ? (r.kind === 'agent' ? 'An agent is on it…' : 'Running…')
      : r.status === 'never' ? 'Has not run yet.'
      : (r.status === 'failed' ? 'Failed: ' : '') + r.summary;
    var when = [r.schedule_text];
    if (r.last_run && !r.running) when.push('last ran ' + rel(r.last_run));
    if (!r.enabled) when.push('switched off');
    else if (r.next_run && !r.running) when.push(r.next_run <= Date.now() / 1000 ? 'due now' : 'next ' + rel(r.next_run));
    return '<div class="' + cls + '" data-id="' + esc(r.id) + '"><div class="ccck-ico">' + esc(r.icon) + '</div><div class="ccck-main">' +
      '<div class="ccck-name">' + esc(r.name) + '</div><div class="ccck-does">' + esc(r.does) + '</div>' +
      '<div class="ccck-say">' + esc(say) + '</div><div class="ccck-when">' + esc(when.join(' · ')) + '</div>' +
      '<div class="ccck-acts"><button type="button" class="ccck-sw" role="switch" aria-checked="' + r.enabled + '" aria-label="' + esc(r.name) + ' on or off" data-k="toggle"></button>' +
      '<button type="button" class="ccck-btn" data-k="run"' + (r.running ? ' disabled' : '') + '>Run now</button>' +
      (r.kind === 'agent' && r.session_id && !thumb ? '<button type="button" class="ccck-btn" data-k="open">See its work</button>' : '') +
      '</div></div></div>';
  }
  function draw() {
    if (!list || !state) return;
    var html = state.routines.map(card).join('');
    if (html === lastDrawn) return;
    lastDrawn = html; list.innerHTML = html;
  }
  function refresh() {
    return api('/api/apps/routines?app=' + encodeURIComponent(APP)).then(function (r) {
      if (!r.ok) return;
      state = r; draw();
      var id = (r.agent && r.agent.session_id) || '';
      if (shell && id !== agentId) { agentId = id; if (id) showSession(id, 'Agent'); }
      else if (shell && !id && !hero.dataset.asked) { hero.dataset.asked = '1'; noAgent(); }
    });
  }
  function buildRoutines(content) {
    var host = content.querySelector('[data-ccc-routines]');
    if (!host) { host = $('section'); host.innerHTML = '<h2>Routines</h2>'; content.appendChild(host); }
    var box = $('div', 'ccck-routines');
    list = $('div', 'ccck-list');
    box.appendChild(list);
    host.appendChild(box);
    list.addEventListener('click', function (e) {
      var b = e.target.closest('[data-k]'), c = e.target.closest('.ccck-r');
      if (!b || !c) return;
      var id = c.dataset.id, r = state.routines.filter(function (x) { return x.id === id; })[0];
      if (b.dataset.k === 'run') { b.disabled = true; api('/api/apps/routines/run', { app: APP, id: id }).then(function () { setTimeout(refresh, 400); setTimeout(refresh, 2500); }); }
      else if (b.dataset.k === 'toggle') api('/api/apps/routines/toggle', { app: APP, id: id, enabled: !r.enabled }).then(refresh);
      else if (b.dataset.k === 'open' && shell) { showSession(r.session_id, r.name); setMode('agent'); }
    });
    if (thumb) return;
    var add = $('div', 'ccck-r ccck-new', '<div class="ccck-name">＋ New routine</div><textarea placeholder="What should it do, and how often? For example: every morning, tell me if hotel prices dropped." aria-label="Describe the new routine"></textarea><div class="ccck-acts"><button type="button" class="ccck-btn pri">Ask the agent to build it</button></div><div class="ccck-note" hidden></div>');
    box.appendChild(add);
    var ta = add.querySelector('textarea'), go = add.querySelector('button'), note = add.querySelector('.ccck-note');
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
