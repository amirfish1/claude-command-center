/* Question tags: turns `[Q<n>:<slug>]` in session messages into chips.
 * Tapping a chip shows the first message that carried the tag (the full
 * explanation) without scrolling up, and a pill lists questions still open.
 * Backed by GET /api/question-tags (ccc_server/question_tags.py).
 *
 * Self-contained: one MutationObserver, one cached index fetch (refreshed
 * lazily on a chip tap / panel open and every 60s while visible).
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else {
    root.cccQuestionTags = api;
    if (typeof document !== 'undefined') api.boot();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var TAG_RE = /\[Q(\d+):([a-z0-9-]+)\]/g;
  var TARGETS = '.assistant-text, .user-msg';
  var POLL_MS = 60000;
  var index = { byN: {}, list: [], scanning: false, at: 0 };

  function splitTags(text) {
    var out = [], last = 0, m;
    TAG_RE.lastIndex = 0;
    while ((m = TAG_RE.exec(text))) {
      if (m.index > last) out.push({ text: text.slice(last, m.index) });
      out.push({ n: +m[1], slug: m[2], tag: m[0] });
      last = m.index + m[0].length;
    }
    if (last < text.length) out.push({ text: text.slice(last) });
    return out;
  }

  function openQuestions(list) { return (list || []).filter(function (q) { return !q.answered; }); }

  function chipify(rootEl) {
    var hosts = rootEl.matches && rootEl.matches(TARGETS) ? [rootEl] : [];
    if (rootEl.querySelectorAll) hosts = hosts.concat(Array.from(rootEl.querySelectorAll(TARGETS)));
    hosts.forEach(function (host) {
      var walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT, {
        acceptNode: function (n) {
          if (n.nodeValue.indexOf('[Q') < 0) return NodeFilter.FILTER_REJECT;
          if (n.parentElement && n.parentElement.closest('.qtag-chip, pre, code, textarea, script')) return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_ACCEPT;
        },
      });
      var nodes = [], n;
      while ((n = walker.nextNode())) nodes.push(n);
      nodes.forEach(function (node) {
        var parts = splitTags(node.nodeValue);
        if (!parts.some(function (p) { return p.tag; })) return;
        var frag = document.createDocumentFragment();
        parts.forEach(function (p) {
          if (!p.tag) { frag.appendChild(document.createTextNode(p.text)); return; }
          var b = document.createElement('button');
          b.type = 'button';
          b.className = 'qtag-chip';
          b.setAttribute('data-qn', p.n);
          b.textContent = p.tag;
          frag.appendChild(b);
        });
        node.parentNode.replaceChild(frag, node);
      });
    });
    paintChips();
  }

  function paintChips() {
    document.querySelectorAll('.qtag-chip').forEach(function (c) {
      var q = index.byN[c.getAttribute('data-qn')];
      c.classList.toggle('qtag-answered', !!(q && q.answered));
      c.classList.toggle('qtag-unknown', !q);
    });
  }

  function load(force) {
    if (!force && Date.now() - index.at < 5000) return Promise.resolve(index);
    return fetch('/api/question-tags').then(function (r) { return r.json(); }).then(function (d) {
      if (!d || !d.ok) return index;
      index.list = d.questions || [];
      index.byN = {};
      index.list.forEach(function (q) { index.byN[q.n] = q; });
      index.scanning = !!d.scanning;
      index.refreshing = !!d.refreshing;
      index.at = Date.now();
      paintChips();
      updatePill();
      return index;
    }).catch(function () { return index; });
  }

  /* ---------------- UI ---------------- */
  var sheet, pill;

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; });
  }
  function fmtTs(ts) { try { return new Date(ts).toLocaleString(); } catch (_) { return ts || ''; } }

  function ensureSheet() {
    if (sheet) return sheet;
    sheet = document.createElement('div');
    sheet.className = 'qtag-sheet';
    sheet.hidden = true;
    sheet.addEventListener('click', function (ev) {
      if (ev.target === sheet || ev.target.closest('.qtag-close')) { sheet.hidden = true; return; }
      var jump = ev.target.closest('[data-qjump]');
      if (jump) { jumpTo(+jump.getAttribute('data-qjump')); return; }
      var open = ev.target.closest('[data-qshow]');
      if (open) showQuestion(+open.getAttribute('data-qshow'));
    });
    document.body.appendChild(sheet);
    return sheet;
  }

  // A tag from a message written seconds ago may not be indexed yet: the
  // server rescans in the background, so retry a few times before giving up.
  function lookup(n, attempt) {
    load(true).then(function () {
      if (index.byN[n] || attempt >= 4 || !(index.scanning || index.refreshing)) { showQuestion(n); return; }
      showQuestion(n, true);
      setTimeout(function () { if (!sheet.hidden && sheet.getAttribute('data-qwait') === String(n)) lookup(n, attempt + 1); }, 3000);
    });
  }

  function showQuestion(n, waiting) {
    var q = index.byN[n];
    var s = ensureSheet();
    var body;
    if (!q) {
      body = '<p class="qtag-empty">' + (waiting ? 'Looking for Q' + n + '…' : 'No explanation found for Q' + n + ' yet.') + '</p>';
    } else {
      body = '<div class="qtag-meta">' + (q.answered ? '<span class="qtag-st qtag-st-done">answered</span>' : '<span class="qtag-st qtag-st-open">open</span>')
        + ' first asked ' + esc(fmtTs(q.first.ts)) + ' · ' + q.mentions.length + ' reminder' + (q.mentions.length === 1 ? '' : 's') + '</div>'
        + '<pre class="qtag-text">' + esc(q.first.text) + '</pre>'
        + '<button type="button" class="qtag-btn" data-qjump="' + q.n + '">Jump to original message</button>';
    }
    s.innerHTML = '<div class="qtag-card"><div class="qtag-head"><strong>' + (q ? esc(q.tag) : 'Q' + n) + '</strong>'
      + '<button type="button" class="qtag-close" aria-label="Close">×</button></div>' + body + '</div>';
    if (waiting) s.setAttribute('data-qwait', n); else s.removeAttribute('data-qwait');
    s.hidden = false;
  }

  function showPanel() {
    var s = ensureSheet();
    var open = openQuestions(index.list);
    s.innerHTML = '<div class="qtag-card"><div class="qtag-head"><strong>Open questions (' + open.length + ')</strong>'
      + '<button type="button" class="qtag-close" aria-label="Close">×</button></div>'
      + (open.length ? '<ul class="qtag-list">' + open.map(function (q) {
        var first = (q.first.text.split('\n').find(function (l) { return l.indexOf(q.tag) >= 0; }) || q.first.text).replace(/[#*`]/g, '').slice(0, 140);
        return '<li><button type="button" class="qtag-row" data-qshow="' + q.n + '"><span class="qtag-chip-static">' + esc(q.tag) + '</span>'
          + '<span class="qtag-sub">' + esc(first) + '</span></button></li>';
      }).join('') + '</ul>' : '<p class="qtag-empty">' + (index.scanning ? 'Building the question index (first run only)…' : 'Nothing waiting on you.') + '</p>')
      + '</div>';
    s.hidden = false;
  }

  function jumpTo(n) {
    var q = index.byN[n];
    if (!q) return;
    sheet.hidden = true;
    if (typeof window.cccOpenSession === 'function') window.cccOpenSession(q.first.sid);
    var tries = 0;
    (function seek() {
      var el = document.querySelector('.qtag-chip[data-qn="' + n + '"]:not(.qtag-row .qtag-chip)');
      if (el) {
        el.scrollIntoView({ block: 'center', behavior: 'smooth' });
        el.classList.add('qtag-flash');
        setTimeout(function () { el.classList.remove('qtag-flash'); }, 2500);
      } else if (++tries < 20) setTimeout(seek, 300);
    })();
  }

  function updatePill() {
    if (!pill) return;
    var n = openQuestions(index.list).length;
    pill.hidden = n === 0;
    pill.textContent = '? ' + n;
    pill.title = n + ' open question' + (n === 1 ? '' : 's');
  }

  function boot() {
    if (document.querySelector('.qtag-pill')) return;
    pill = document.createElement('button');
    pill.type = 'button';
    pill.className = 'qtag-pill';
    pill.hidden = true;
    pill.addEventListener('click', function () { load(true).then(showPanel); });
    document.body.appendChild(pill);

    document.addEventListener('click', function (ev) {
      var chip = ev.target.closest && ev.target.closest('.qtag-chip[data-qn]');
      if (!chip) return;
      ev.preventDefault();
      lookup(+chip.getAttribute('data-qn'), 0);
    });

    var pending = new Set(), queued = false;
    var mo = new MutationObserver(function (muts) {
      muts.forEach(function (m) {
        m.addedNodes.forEach(function (nd) {
          if (nd.nodeType === 1 && nd.className !== 'qtag-chip') pending.add(nd);
          else if (nd.nodeType === 3 && nd.parentElement) pending.add(nd.parentElement);
        });
        if (m.type === 'characterData' && m.target.parentElement) pending.add(m.target.parentElement);
      });
      if (queued || !pending.size) return;
      queued = true;
      requestAnimationFrame(function () {
        queued = false;
        var batch = Array.from(pending); pending.clear();
        mo.disconnect();
        batch.forEach(function (el) {
          if (!el.isConnected) return;
          var host = el.closest ? el.closest(TARGETS) : null;
          chipify(host || el);
        });
        mo.observe(document.body, { childList: true, subtree: true, characterData: true });
      });
    });
    mo.observe(document.body, { childList: true, subtree: true, characterData: true });
    chipify(document.body);
    load(true);
    setInterval(function () { if (!document.hidden) load(true); }, POLL_MS);
  }

  return { boot: boot, splitTags: splitTags, openQuestions: openQuestions, chipify: chipify };
});
