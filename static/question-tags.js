/* Question tags: turns `[Q<n>:<slug>]` in session messages into chips.
 * Tapping a chip shows the first message that carried the tag (the full
 * explanation) without scrolling up, and a pill lists questions still open.
 * Backed by GET /api/question-tags (ccc_server/question_tags.py).
 *
 * A question a ticket blocked on goes by that ticket instead: its chip shows
 * the ticket ref and opens the ticket, and "Needs you" gains Talk to worker.
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

  // Full tag, slugless [Q<n>], or a bare Q<n> (2+ digits). Agents often
  // restate a question as plain "Q528" (Codex session-state cards do), so the
  // short forms chip too, but only for numbers the index knows (CCC-258).
  var TAG_RE = /\[Q(\d+):([a-z0-9-]+)\]|\[Q(\d+)\]|\bQ(\d{2,})\b/g;
  var HAS_REF_RE = /Q\d/;
  var TARGETS = '.assistant-text, .user-msg, .conv-outcome-step, .wa-preview, .session-state-block';
  var POLL_MS = 60000;
  var index = { byN: {}, list: [], scanning: false, at: 0 };
  var loadGeneration = 0;

  // known(n) gates the short forms; without it only full tags split out.
  function splitTags(text, known) {
    var out = [], last = 0, m;
    TAG_RE.lastIndex = 0;
    while ((m = TAG_RE.exec(text))) {
      var n = +(m[1] || m[3] || m[4]);
      if (!m[1] && !(known && known(n))) continue;
      if (m.index > last) out.push({ text: text.slice(last, m.index) });
      out.push({ n: n, slug: m[2] || '', tag: m[0], short: !m[1] });
      last = m.index + m[0].length;
    }
    if (last < text.length) out.push({ text: text.slice(last) });
    return out;
  }

  function knownQuestion(n) { return !!index.byN[n]; }

  function openQuestions(list) { return (list || []).filter(function (q) { return !q.answered; }); }

  function chipify(rootEl, skipPaint) {
    var hosts = rootEl.matches && rootEl.matches(TARGETS) ? [rootEl] : [];
    if (rootEl.querySelectorAll) hosts = hosts.concat(Array.from(rootEl.querySelectorAll(TARGETS)));
    hosts.forEach(function (host) {
      var walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT, {
        acceptNode: function (n) {
          if (!HAS_REF_RE.test(n.nodeValue)) return NodeFilter.FILTER_REJECT;
          if (n.parentElement && n.parentElement.closest('.qtag-chip, pre, code, textarea, script')) return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_ACCEPT;
        },
      });
      var nodes = [], n;
      while ((n = walker.nextNode())) nodes.push(n);
      nodes.forEach(function (node) {
        var parts = splitTags(node.nodeValue, knownQuestion);
        if (!parts.some(function (p) { return p.tag; })) return;
        var frag = document.createDocumentFragment();
        parts.forEach(function (p) {
          if (!p.tag) { frag.appendChild(document.createTextNode(p.text)); return; }
          var b = document.createElement('button');
          b.type = 'button';
          b.className = 'qtag-chip';
          b.setAttribute('data-qn', p.n);
          var q = index.byN[p.n];
          var full = p.short && q && q.tag ? q.tag : p.tag;
          b.textContent = p.short || host.closest('.conv-item') ? 'Q' + p.n : p.tag;
          b.title = full + ' — view question and answer';
          b.setAttribute('aria-label', b.title);
          frag.appendChild(b);
        });
        node.parentNode.replaceChild(frag, node);
      });
    });
    if (!skipPaint) paintChips();
  }

  // "BECKY-1 is blocked on BECKY-1" says nothing: right after its own ticket
  // ref, the chip names the question instead.
  function followsRef(chip, ref) {
    var host = chip.closest(TARGETS);
    if (!host) return false;
    var range = document.createRange();
    range.setStart(host, 0);
    range.setEndBefore(chip);
    return range.toString().slice(-60).indexOf(ref) >= 0;
  }

  function paintTicketChip(chip, q) {
    var label = followsRef(chip, q.ticket.ref) ? (q.ticket.open ? 'open question' : 'question') : q.ticket.ref;
    // Writing unchanged text would wake the observer that repaints chips.
    if (chip.textContent !== label) chip.textContent = label;
    chip.title = q.ticket.ref + ' (' + q.tag + ') — open ticket';
    chip.setAttribute('aria-label', chip.title);
  }

  function talkButton(n) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'ssb-action';
    b.setAttribute('data-qtalk', n);
    b.title = 'Open the worker\'s session to discuss this. Nothing is sent.';
    b.textContent = 'Talk to worker';
    return b;
  }

  function paintNeedsYou() {
    document.querySelectorAll('.ssb-next').forEach(function (row) {
      var talk = row.querySelector('[data-qtalk]');
      var waiting = Array.prototype.map.call(row.querySelectorAll('.qtag-chip'), function (c) {
        return index.byN[c.getAttribute('data-qn')];
      }).filter(function (q) { return q && q.ticket && q.ticket.open; })[0];
      if (!waiting) { if (talk) talk.remove(); return; }
      if (talk) return;
      var actions = row.querySelector('.ssb-actions');
      if (!actions) {
        actions = document.createElement('span');
        actions.className = 'ssb-actions';
        row.append(' ', actions);
      }
      actions.append(' ', talkButton(waiting.n));
    });
  }

  function paintChips() {
    document.querySelectorAll('.qtag-chip').forEach(function (c) {
      var q = index.byN[c.getAttribute('data-qn')];
      c.classList.toggle('qtag-answered', !!(q && q.answered));
      c.classList.toggle('qtag-unknown', !q);
      if (q && q.ticket) paintTicketChip(c, q);
    });
    paintNeedsYou();
  }

  function load(force) {
    if (!force && Date.now() - index.at < 5000) return Promise.resolve(index);
    var generation = ++loadGeneration;
    return fetch('/api/question-tags').then(function (r) { return r.json(); }).then(function (d) {
      if (generation !== loadGeneration) return index;
      if (!d || !d.ok) return index;
      var before = index.list.length;
      index.list = d.questions || [];
      index.byN = {};
      index.list.forEach(function (q) { index.byN[q.n] = q; });
      // Short refs chip only once their number is known: rescan when the
      // set of known questions grows (first load, new questions).
      if (index.list.length > before && typeof document !== 'undefined') chipify(document.body);
      index.scanning = !!d.scanning;
      index.refreshing = !!d.refreshing;
      index.at = Date.now();
      paintChips();
      updatePill();
      return index;
    }).catch(function () { return index; });
  }

  /* ---------------- UI ---------------- */
  var sheet, pill, activeQuestion;
  var pillHidden = false, pillPosition = null;
  var PILL_PREFS = 'ccc-question-pill';

  function savePill() {
    try { localStorage.setItem(PILL_PREFS, JSON.stringify({ hidden: pillHidden, position: pillPosition })); } catch (_) {}
  }

  function positionPill() {
    if (!pillPosition || !pill || pill.hidden || !pill.offsetWidth) return;
    var viewport = window.visualViewport;
    var left = viewport ? viewport.offsetLeft : 0, top = viewport ? viewport.offsetTop : 0;
    var width = viewport ? viewport.width : window.innerWidth;
    var height = viewport ? viewport.height : window.innerHeight;
    pill.style.left = Math.max(left + 8, Math.min(pillPosition.x, left + width - pill.offsetWidth - 8)) + 'px';
    pill.style.top = Math.max(top + 8, Math.min(pillPosition.y, top + height - pill.offsetHeight - 8)) + 'px';
    pill.style.right = 'auto';
    pill.style.bottom = 'auto';
  }

  function bindPillGestures() {
    var drag, timer, suppressClick = false, suppressTimer;
    function suppress() {
      suppressClick = true;
      clearTimeout(suppressTimer);
      suppressTimer = setTimeout(function () { suppressClick = false; }, 400);
    }
    function options() {
      clearTimeout(timer);
      drag = null;
      suppress();
      showPanel();
      var toggle = sheet.querySelector('[data-qpill-toggle]');
      if (toggle) toggle.focus();
    }
    pill.addEventListener('pointerdown', function (ev) {
      if (!ev.isPrimary || ev.button !== 0 || drag) return;
      var rect = pill.getBoundingClientRect();
      drag = { id: ev.pointerId, x: ev.clientX, y: ev.clientY, left: rect.left, top: rect.top, moved: false };
      timer = setTimeout(options, 600);
      try { pill.setPointerCapture(ev.pointerId); } catch (_) {}
    });
    window.addEventListener('pointermove', function (ev) {
      if (!drag || ev.pointerId !== drag.id) return;
      var dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) < 8) return;
      clearTimeout(timer);
      drag.moved = true;
      ev.preventDefault();
      pillPosition = { x: drag.left + dx, y: drag.top + dy };
      positionPill();
    }, { passive: false });
    function end(ev) {
      if (!drag || ev.pointerId !== drag.id) return;
      clearTimeout(timer);
      if (drag.moved) { savePill(); suppress(); }
      drag = null;
    }
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);
    pill.addEventListener('lostpointercapture', end);
    pill.addEventListener('contextmenu', function (ev) { ev.preventDefault(); options(); });
    pill.addEventListener('keydown', function (ev) {
      if (ev.key === 'ContextMenu' || (ev.shiftKey && ev.key === 'F10')) { ev.preventDefault(); options(); }
    });
    pill.addEventListener('click', function (ev) {
      if (suppressClick) { suppressClick = false; ev.preventDefault(); return; }
      load(true).then(showPanel);
    });
    window.addEventListener('blur', function () { clearTimeout(timer); drag = null; });
    window.addEventListener('resize', positionPill);
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', positionPill);
      window.visualViewport.addEventListener('scroll', positionPill);
    }
  }

  function questionName(q) { return q.ticket ? q.ticket.ref : q.tag; }

  // A reply chip for a question a ticket is waiting on must answer the ticket:
  // a chat message reaches the worker but leaves the ticket blocked. Returns
  // null for any other reply, which stays an ordinary chat message.
  function answerTicket(reply) {
    var tag = /\[Q(\d+)[:\]]/.exec(reply || '');
    var q = tag && index.byN[tag[1]];
    if (!q || !q.ticket || !q.ticket.open) return null;
    return fetch('/api/ux-fixes/answer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ref: q.ticket.ref, text: reply }),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok || !d.ok) throw new Error(d.error || 'HTTP ' + r.status);
        q.ticket.open = false;
        q.answered = true;
        paintChips();
        updatePill();
        return q.ticket.ref;
      });
    });
  }

  function talkToWorker(n) {
    var q = index.byN[n];
    if (!q) return;
    if (sheet) sheet.hidden = true;
    window.cccFocusComposer(q.ticket && q.ticket.session_id || q.first.sid);
  }

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
      if (ev.target.closest('[data-qpill-toggle]')) {
        pillHidden = !pillHidden;
        savePill();
        updatePill();
        sheet.hidden = true;
        if (!pill.hidden) pill.focus();
        return;
      }
      var jump = ev.target.closest('[data-qjump]');
      if (jump) { jumpTo(+jump.getAttribute('data-qjump')); return; }
      if (ev.target.closest('[data-qlist]')) { showPanel(); return; }
      var ticket = ev.target.closest('[data-qticket]');
      if (ticket) { sheet.hidden = true; window.cccOpenTicket(ticket.getAttribute('data-qticket')); return; }
      var send = ev.target.closest('[data-qsend]');
      if (send) { sendAnswer(+send.getAttribute('data-qsend'), send); return; }
      var add = ev.target.closest('[data-qadd]');
      if (add) { addAnswer(+add.getAttribute('data-qadd')); return; }
      var open = ev.target.closest('[data-qshow]');
      if (open) { activeQuestion = null; showQuestion(+open.getAttribute('data-qshow')); }
    });
    document.body.appendChild(sheet);
    return sheet;
  }

  // A tag from a message written seconds ago may not be indexed yet: the
  // server rescans in the background, so retry a few times before giving up.
  function lookup(n, attempt, context) {
    load(true).then(function () {
      if (context !== activeQuestion) return;
      if (index.byN[n] || attempt >= 4 || !(index.scanning || index.refreshing)) { showQuestion(n); return; }
      showQuestion(n, true);
      setTimeout(function () { if (!sheet.hidden && context === activeQuestion && sheet.getAttribute('data-qwait') === String(n)) lookup(n, attempt + 1, context); }, 3000);
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
        // A ticket question is answered on the ticket: a chat reply can't unblock it.
        + (q.ticket ? '<div class="qtag-actions"><button type="button" class="qtag-btn qtag-primary" data-qticket="' + esc(q.ticket.ref) + '">Open ticket</button>'
          + (q.ticket.open ? '<button type="button" class="qtag-btn" data-qtalk="' + q.n + '">Talk to worker</button>' : '')
          : '<label class="qtag-ans-label" for="qtagAns">Your answer</label>'
          + '<textarea id="qtagAns" class="qtag-ans" rows="3" placeholder="Type your answer, then choose Send answer or Add to composer."></textarea>'
          + '<div class="qtag-actions"><button type="button" class="qtag-btn qtag-primary" data-qsend="' + q.n + '">Send answer</button>'
          + '<button type="button" class="qtag-btn" data-qadd="' + q.n + '">Add to composer</button>')
        + '<button type="button" class="qtag-btn" data-qjump="' + q.n + '">Jump to original</button>'
        + (openQuestions(index.list).length > 1 ? '<button type="button" class="qtag-btn" data-qlist="1">All open questions</button>' : '')
        + '</div><div class="qtag-added" aria-live="polite"></div>';
    }
    s.innerHTML = '<div class="qtag-card"><div class="qtag-head"><strong>' + (q ? esc(questionName(q)) : 'Q' + n) + '</strong>'
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
    var answerSid = activeQuestion && activeQuestion.n === n && activeQuestion.sid || q.first.sid;
    window.cccAppendToComposer(answerSid, '[Q' + n + '] ' + txt).then(function (ok) {
      if (!note) return;
      if (ok) { ta.value = ''; note.textContent = 'Added to the composer for that session (not sent). Add more answers, or close and send when ready.'; }
      else note.textContent = 'Could not reach that session\'s composer. Open the session and try again.';
    });
  }

  var sending = new Set();
  function sendAnswer(n, button) {
    var q = index.byN[n], ta = document.getElementById('qtagAns'), note = sheet.querySelector('.qtag-added');
    var txt = ta && ta.value.trim();
    if (!q || !txt) { if (note) note.textContent = 'Type an answer first.'; return; }
    var sid = activeQuestion && activeQuestion.n === n && activeQuestion.sid || q.first.sid;
    var key = sid + ':' + n;
    if (sending.has(key)) return;
    sending.add(key);
    button.disabled = true;
    if (note) note.textContent = 'Sending…';
    Promise.resolve().then(function () {
      return window.cccSendQuestionAnswer(sid, '[Q' + n + '] ' + txt);
    }).then(function (result) {
      if (!result || !result.ok) throw new Error(result && result.error || 'Send failed');
      if (ta.value.trim() === txt) ta.value = '';
      if (note) note.textContent = result.queued ? 'Queued for this session.' : 'Sent to this session.';
    }).catch(function (error) {
      if (note) note.textContent = 'Could not send: ' + error.message + '. Your answer is still here.';
    }).finally(function () { sending.delete(key); button.disabled = false; });
  }

  function showPanel() {
    activeQuestion = null;
    var s = ensureSheet();
    var open = openQuestions(index.list);
    s.innerHTML = '<div class="qtag-card"><div class="qtag-head"><strong>Open questions (' + open.length + ')</strong>'
      + '<button type="button" class="qtag-close" aria-label="Close">×</button></div>'
      + '<button type="button" class="qtag-btn qtag-pill-toggle" data-qpill-toggle>' + (pillHidden ? 'Show floating question button' : 'Hide floating question button') + '</button>'
      + (open.length ? '<ul class="qtag-list">' + open.map(function (q) {
        var first = (q.first.text.split('\n').find(function (l) { return l.indexOf(q.tag) >= 0; }) || q.first.text).replace(/[#*`]/g, '').slice(0, 140);
        return '<li><button type="button" class="qtag-row" data-qshow="' + q.n + '"><span class="qtag-chip-static">' + esc(questionName(q)) + '</span>'
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
    pill.hidden = n === 0 || pillHidden;
    pill.textContent = '? ' + n;
    pill.title = n + ' open question' + (n === 1 ? '' : 's') + ' — drag to move; hold to hide';
    pill.setAttribute('aria-label', pill.title);
    positionPill();
    // Menu copies (phones fold the pill into the ⋮ menus) and the count
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
    try {
      var prefs = JSON.parse(localStorage.getItem(PILL_PREFS) || 'null');
      pillHidden = !!(prefs && prefs.hidden);
      if (prefs && prefs.position && Number.isFinite(prefs.position.x) && Number.isFinite(prefs.position.y)) pillPosition = prefs.position;
    } catch (_) {}
    bindPillGestures();
    document.body.appendChild(pill);

    document.addEventListener('click', function (ev) {
      var talk = ev.target.closest && ev.target.closest('[data-qtalk]');
      if (talk) {
        ev.preventDefault();
        ev.stopPropagation();
        talkToWorker(+talk.getAttribute('data-qtalk'));
        return;
      }
      var chip = ev.target.closest && ev.target.closest('.qtag-chip[data-qn]');
      if (!chip) return;
      ev.preventDefault();
      ev.stopPropagation();
      var bound = index.byN[chip.getAttribute('data-qn')];
      if (bound && bound.ticket && window.cccOpenTicket && window.cccOpenTicket(bound.ticket.ref)) return;
      var row = chip.closest('.conv-item[data-session-id]');
      var sid = row ? row.getAttribute('data-session-id')
        : (typeof window.cccFrontSessionId === 'function' ? window.cccFrontSessionId() : '');
      activeQuestion = {n: +chip.getAttribute('data-qn'), sid: sid};
      lookup(activeQuestion.n, 0, activeQuestion);
    }, true); // capture: sidebar rows stop click propagation before it bubbles here

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
      // Microtask, not rAF: chip before the browser paints, so freshly rendered
      // text never shows one frame of raw "[Q12:slug]" then reflows (CCC-335).
      Promise.resolve().then(function () {
        queued = false;
        var batch = Array.from(pending); pending.clear();
        mo.disconnect();
        // One transcript render adds hundreds of nodes under a few hosts:
        // visit each host once and repaint once, not once per node (CCC-143).
        var roots = new Set();
        batch.forEach(function (el) {
          if (!el.isConnected) return;
          roots.add((el.closest ? el.closest(TARGETS) : null) || el);
        });
        roots.forEach(function (root) { chipify(root, true); });
        if (roots.size) paintChips();
        mo.observe(document.body, { childList: true, subtree: true, characterData: true });
      });
    });
    mo.observe(document.body, { childList: true, subtree: true, characterData: true });
    chipify(document.body);
    load(true);
    setInterval(function () { if (!document.hidden) load(true); }, POLL_MS);
  }

  function open(n, sid) {
    activeQuestion = {n: n, sid: sid};
    lookup(n, 0, activeQuestion);
  }

  return { open: open, answerTicket: answerTicket, boot: boot, splitTags: splitTags, openQuestions: openQuestions, chipify: chipify };
});
