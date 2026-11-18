// Copyright (c) 2026 Amir Fish. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-MIT
//
// "What do you want an app for?" box. Drop <div data-ccc-concierge></div>
// on a page and load this script; it asks the Concierge to set up the app,
// then opens it.
(function () {
  'use strict';
  var css = '.ccc-con{max-width:560px;margin:24px auto;padding:22px;border-radius:16px;text-align:center;font:15px/1.5 system-ui,sans-serif;'
    + 'background:var(--k-panel,var(--panel,#161a21));color:var(--k-text,var(--text,#edf0f5));border:1px solid var(--k-line,var(--border,#2b323d))}'
    + '.ccc-con h2{margin:0 0 4px;font-size:22px}.ccc-con .em{font-size:40px}'
    + '.ccc-con p{margin:0 0 14px;color:var(--k-muted,var(--muted,#a8b2c2))}'
    + '.ccc-con textarea{width:100%;box-sizing:border-box;min-height:64px;border-radius:10px;padding:10px;font:inherit;resize:vertical;'
    + 'background:var(--k-tile,var(--bg,#1d232d));color:inherit;border:1px solid var(--k-line,var(--border,#2b323d))}'
    + '.ccc-con button{margin-top:10px;padding:9px 20px;border:0;border-radius:999px;font:inherit;font-weight:600;cursor:pointer;'
    + 'background:var(--k-accent,var(--accent,#80b9ff));color:#0b1220}.ccc-con button:disabled{opacity:.6;cursor:wait}'
    + '.ccc-con .note{margin-top:10px;min-height:1.2em;color:var(--k-muted,var(--muted,#a8b2c2))}';
  function mount(host) {
    if (host.dataset.cccBuilt) return;
    host.dataset.cccBuilt = '1';
    if (!document.getElementById('ccc-con-css')) {
      var s = document.createElement('style'); s.id = 'ccc-con-css'; s.textContent = css; document.head.appendChild(s);
    }
    host.className = 'ccc-con';
    host.innerHTML = '<div class="em">✨</div><h2>What do you want an app for?</h2>'
      + '<p>Say it in one sentence. An agent sets it up and asks you a few questions.</p>'
      + '<textarea placeholder="Keep track of my ski trips and watch the snow"></textarea>'
      + '<div><button type="button">Make my app</button></div><div class="note"></div>';
    var ta = host.querySelector('textarea'), btn = host.querySelector('button'), note = host.querySelector('.note');
    btn.addEventListener('click', function () {
      var wish = ta.value.trim();
      if (!wish) { ta.focus(); return; }
      btn.disabled = true; note.textContent = 'Setting things up…';
      fetch('/api/apps/concierge', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ wish: wish }) })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (!j.ok) throw new Error(j.error || 'failed');
          note.textContent = 'Ready. Opening your app…';
          location.href = j.url;
        })
        .catch(function (e) { note.textContent = 'Could not set it up: ' + (e.message || e); btn.disabled = false; });
    });
  }
  function boot() { Array.prototype.forEach.call(document.querySelectorAll('[data-ccc-concierge]'), mount); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
  window.cccConcierge = { mount: mount };
})();
