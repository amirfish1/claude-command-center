// Copyright (c) 2026 Amir Fish. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-MIT
//
// App feed: a visual timeline of findings from research routines.
//
//   <div data-ccc-feed></div>
//   <script src="/static/app-feed.js" data-app="my-app" defer></script>
//
// Cards come from the app's feed.json ({at,title,body,url,image,routine}),
// appended by POST /api/apps/feed. Newest first; refreshes on its own.
(function () {
  'use strict';
  var me = document.currentScript, APP = me && me.dataset.app;
  if (!APP) return;
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };
  var okUrl = function (u) { return /^(https?:\/\/|\/(?!\/)|data:image\/)/i.test(u || ''); };
  var rel = function (t) {
    var s = Math.max(0, Date.now() / 1000 - t);
    return s < 90 ? 'just now' : s < 5400 ? Math.round(s / 60) + ' min ago' : s < 129600 ? Math.round(s / 3600) + ' h ago' : Math.round(s / 86400) + ' d ago';
  };
  var day = function (t) {
    var d = new Date(t * 1000), n = new Date(), y = new Date(Date.now() - 864e5);
    if (d.toDateString() === n.toDateString()) return 'Today';
    if (d.toDateString() === y.toDateString()) return 'Yesterday';
    return d.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
  };
  var css = [
    '.cccf{--f-panel:#161a21;--f-line:#2b323d;--f-text:#edf0f5;--f-muted:#a8b2c2;--f-accent:#80b9ff;--f-tile:#1d232d;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--f-text);position:relative;padding-left:22px}',
    '[data-theme="light"] .cccf{--f-panel:#fff;--f-line:#d9dee6;--f-text:#1a1f29;--f-muted:#5d6878;--f-accent:#1f6fd0;--f-tile:#eef1f5}',
    '.cccf:before{content:"";position:absolute;left:6px;top:6px;bottom:6px;width:2px;background:var(--f-line);border-radius:2px}',
    '.cccf-day{font-weight:700;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--f-muted);margin:16px 0 8px;position:relative}',
    '.cccf-day:before{content:"";position:absolute;left:-21px;top:3px;width:10px;height:10px;border-radius:50%;background:var(--f-accent)}',
    '.cccf-card{background:var(--f-panel);border:1px solid var(--f-line);border-radius:14px;overflow:hidden;margin-bottom:12px;animation:cccf-in .35s ease both}',
    '@keyframes cccf-in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}',
    '.cccf-card img{display:block;width:100%;max-height:260px;object-fit:cover;background:var(--f-tile)}',
    '.cccf-in{padding:12px 14px}.cccf-t{font-weight:650;font-size:15px;margin:0 0 4px}',
    '.cccf-b{color:var(--f-muted);white-space:pre-wrap;overflow-wrap:anywhere;margin:0}',
    '.cccf-m{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:8px;font-size:12px;color:var(--f-muted)}',
    '.cccf-chip{background:var(--f-tile);border-radius:999px;padding:1px 9px}',
    '.cccf a{color:var(--f-accent);text-decoration:none;font-weight:600}.cccf a:hover{text-decoration:underline}',
    '.cccf-empty{color:var(--f-muted);padding:18px;text-align:center;border:1px dashed var(--f-line);border-radius:14px}'
  ].join('');
  var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
  var host;
  function ensureHost() {
    host = document.querySelector('[data-ccc-feed]');
    if (!host) { host = document.createElement('div'); host.setAttribute('data-ccc-feed', ''); (document.querySelector('[data-ccc-content]') || document.querySelector('main') || document.body).appendChild(host); }
    host.classList.add('cccf');
  }
  var last = '';
  function render(items) {
    var sig = JSON.stringify(items);
    if (sig === last) { refreshAges(); return; }
    last = sig;
    if (!items.length) { host.innerHTML = '<div class="cccf-empty">🔭 Nothing found yet. New findings will show up here.</div>'; return; }
    var html = '', cur = '';
    items.forEach(function (i) {
      var d = day(i.at || 0);
      if (d !== cur) { cur = d; html += '<div class="cccf-day">' + esc(d) + '</div>'; }
      var img = okUrl(i.image) ? '<img loading="lazy" alt="" src="' + esc(i.image) + '" onerror="this.remove()">' : '';
      var link = okUrl(i.url) && /^https?:/i.test(i.url) ? '<a href="' + esc(i.url) + '" target="_blank" rel="noopener noreferrer">Open ↗</a>' : '';
      html += '<article class="cccf-card">' + img + '<div class="cccf-in"><h3 class="cccf-t">' + esc(i.title) + '</h3>' +
        (i.body ? '<p class="cccf-b">' + esc(i.body) + '</p>' : '') +
        '<div class="cccf-m"><span data-at="' + (i.at || 0) + '">' + rel(i.at || 0) + '</span>' +
        (i.routine ? '<span class="cccf-chip">' + esc(i.routine) + '</span>' : '') + link + '</div></div></article>';
    });
    host.innerHTML = html;
  }
  function refreshAges() {
    host.querySelectorAll('[data-at]').forEach(function (e) { e.textContent = rel(+e.dataset.at); });
  }
  function load() {
    if (document.hidden) return;
    fetch('/api/apps/feed?app=' + encodeURIComponent(APP), { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (d) { if (d && d.ok) render(d.items || []); })
      .catch(function () {});
  }
  ensureHost(); load();
  setInterval(load, 15000);
  document.addEventListener('visibilitychange', load);
})();
