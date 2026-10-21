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
      if (ev.target.closest('[data-qlist]')) { showPanel(); return; }
      var add = ev.target.closest('[data-qadd]');
      if (add) { addAnswer(+add.getAttribute('data-qadd')); return; }
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
      var md = typeof window.cccRenderMarkdown === 'function' ? window.cccRenderMarkdown(q.first.text) : null;
      body = '<div class="qtag-meta">' + (q.answered ? '<span class="qtag-st qtag-st-done">answered</span>' : '<span class="qtag-st qtag-st-open">open</span>')
        + ' first asked ' + esc(fmtTs(q.first.ts)) + ' · ' + q.mentions.length + ' reminder' + (q.mentions.length === 1 ? '' : 's') + '</div>'
        + '<div class="qtag-text assistant-text-plain">' + (md || '<p style="white-space:pre-wrap">' + esc(q.first.text) + '</p>') + '</div>'
        + '<label class="qtag-ans-label" for="qtagAns">Your answer</label>'
        + '<textarea id="qtagAns" class="qtag-ans" rows="3" placeholder="Type your answer. It goes into the session\'s composer; nothing is sent."></textarea>'
        + '<div class="qtag-actions"><button type="button" class="qtag-btn qtag-primary" data-qadd="' + q.n + '">Add to composer</button>'
        + '<button type="button" class="qtag-btn" data-qjump="' + q.n + '">Jump to original</button>'
        + (openQuestions(index.list).length > 1 ? '<button type="button" class="qtag-btn" data-qlist="1">All open questions</button>' : '')
        + '</div><div class="qtag-added" aria-live="polite"></div>';
    }
    s.innerHTML = '<div class="qtag-card"><div class="qtag-head"><strong>' + (q ? esc(q.tag) : 'Q' + n) + '</strong>'
      + '<button type="button" class="qtag-close" aria-label="Close">×</button></div>' + body + '</div>';
    if (waiting) s.setAttribute('data-qwait', n); else s.removeAttribute('data-qwait');
    s.hidden = false;
  }

  function addAnswer(n) {
    var q = index.byN[n], ta = document.getElementById('qtagAns'), note = sheet.querySelector('.qtag-added');
    var txt = ta && ta.value.trim();
    if (!q || !txt) { if (note) note.textContent = 'Type an answer first.'; return; }
    if (note) note.textContent = 'Adding…';
    // Tag the line so the agent (and the open/answered tracker) can tell which question it answers.
    window.cccAppendToComposer(q.first.sid, '[Q' + n + '] ' + txt).then(function (ok) {
      if (!note) return;
      if (ok) { ta.value = ''; note.textContent = 'Added to the composer for that session (not sent). Add more answers, or close and send when ready.'; }
      else note.textContent = 'Could not reach that session\'s composer. Open the session and try again.';
    });
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

  function chipsFor(n) {
    return Array.prototype.slice.call(document.querySelectorAll('.qtag-chip[data-qn="' + n + '"]'));
  }

  // The chip inside the very first message that carried the tag: matched by the
  // event's own timestamp (the dashboard stamps rendered events with data-ts-epoch).
  function firstReferenceChip(q) {
    var want = Date.parse(q.first.ts);
    var chips = chipsFor(q.n);
    if (!isNaN(want)) {
      for (var i = 0; i < chips.length; i++) {
        var ev = chips[i].closest('[data-ts-epoch]');
        if (ev && Math.abs(Number(ev.getAttribute('data-ts-epoch')) - want) <= 5000) return chips[i];
      }
    }
    return null;
  }

  function flash(el) {
    el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    el.classList.add('qtag-flash');
    setTimeout(function () { el.classList.remove('qtag-flash'); }, 2500);
  }

  // Already in the session: keep pressing "Load earlier messages" until the
  // first reference is rendered, then scroll to it. Falls back to the topmost
  // chip when the history is exhausted without a timestamp match.
  function jumpTo(n) {
    var q = index.byN[n];
    if (!q) return;
    sheet.hidden = true;
    var front = typeof window.cccFrontSessionId === 'function' ? window.cccFrontSessionId() : '';
    if (front !== q.first.sid && typeof window.cccOpenSession === 'function') window.cccOpenSession(q.first.sid);
    var started = Date.now(), loads = 0, lastLoadAt = 0;
    (function seek() {
      var hit = firstReferenceChip(q);
      if (hit) { flash(hit); return; }
      var banner = document.querySelector('.conv-load-earlier');
      var loading = banner && (banner.disabled || document.querySelector('.conv-loading-overlay'));
      var timedOut = Date.now() - started > 90000 || loads >= 60;
      if (banner && !loading && !timedOut && Date.now() - lastLoadAt > 400) {
        loads++; lastLoadAt = Date.now();
        banner.click();
      } else if ((!banner && Date.now() - started > 2500) || timedOut) {
        var all = chipsFor(n);
        if (all.length) flash(all[0]);
        return;
      }
      setTimeout(seek, 300);
    })();
  }

  function updatePill() {
    if (!pill) return;
    var n = openQuestions(index.list).length;
    pill.hidden = n === 0;
    pill.textContent = '? ' + n;
    pill.title = n + ' open question' + (n === 1 ? '' : 's');    // Menu copies (phones fold the pill into the ⋮ menus) and the count
    // badge on those menus' buttons.
    document.querySelectorAll('[data-qtag-menu]').forEach(function (el) {
      el.hidden = n === 0;
      el.textContent = 'Open questions (' + n + ')';
    });
    document.querySelectorAll('[data-qtag-badge]').forEach(function (el) {
      el.setAttribute('data-qtag-count', n ? String(n) : '');
    });
  }

  function boot() {
    if (document.querySelector('.qtag-pill')) return;
    pill = document.createElement('button');
    pill.type = 'button';
    pill.className = 'qtag-pill';
    pill.id = 'qtagPill';
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
