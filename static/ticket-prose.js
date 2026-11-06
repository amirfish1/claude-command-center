// ticket-prose.js — shared readable renderer for WatchTower ticket bodies and
// linked-conversation transcripts. Loaded by BOTH the dashboard (index.html /
// app.js ticket modal) and the q2 board (q2.html / q2.js detail pane), which
// deliberately share no other code: this file is the one common rendering
// layer so the two views cannot drift on how a ticket reads (CCC ticket
// legibility, 2026-08-30).
//
// Zero dependencies. Everything is escaped before decoration; the only HTML
// emitted is produced by this file.
(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }
  function escAttr(s) {
    return esc(s).replace(/"/g, '&quot;');
  }

  // ---------------------------------------------------------------- titles --
  // Strip machine noise so `<!-- digest-finding-id: … -->` never becomes the
  // ticket title. Returns cleaned text (may be multi-line); '' if nothing
  // human remains.
  function cleanForTitle(text) {
    var t = String(text || '').replace(/<!--[\s\S]*?-->/g, ' ');
    var lines = t.split(/\r?\n/);
    var kept = [];
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].trim();
      if (!line) continue;
      if (line === 'Fix the following UX issue based on this annotation:') continue;
      if (line.indexOf('Annotation:') === 0) line = line.slice('Annotation:'.length).trim();
      if (line) kept.push(line);
    }
    return kept.join('\n');
  }

  // ------------------------------------------------------ inline decoration --
  // All decorators run on ALREADY-ESCAPED text. Protected spans (links, code)
  // are swapped out for \x00N\x00 markers first so later regexes can't chew
  // on their internals.
  function decorate(escaped) {
    var stash = [];
    function protect(html) {
      stash.push(html);
      return '\x00' + (stash.length - 1) + '\x00';
    }
    var s = escaped;
    // URLs → links (open in new tab)
    s = s.replace(/\bhttps?:\/\/[^\s&]+(?:&amp;[^\s&]+)*/g, function (m) {
      // trailing punctuation shouldn't be part of the link
      var trail = '';
      var mm = m.match(/[.,;:)\]]+$/);
      if (mm) { trail = mm[0]; m = m.slice(0, -trail.length); }
      return protect('<a class="tp-link" href="' + escAttr(m) + '" target="_blank" rel="noopener">' + m + '</a>') + trail;
    });
    // `inline code`
    s = s.replace(/`([^`\n]+)`/g, function (_, body) {
      return protect('<code class="tp-code-inline">' + body + '</code>');
    });
    // **bold**
    s = s.replace(/\*\*([^*\n]+)\*\*/g, function (_, body) {
      return protect('<strong>' + body + '</strong>');
    });
    // conversation keys before bare UUIDs so the whole key highlights as one
    s = s.replace(/\b(?:owner:[0-9a-f-]{36}(?:#b\d+)?|client:[0-9a-f-]{36}:[0-9a-f-]{36})\b/gi, function (m) {
      return protect('<span class="tp-id">' + m + '</span>');
    });
    // UUIDs
    s = s.replace(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi, function (m) {
      return protect('<span class="tp-id">' + m + '</span>');
    });
    // SCREAMING_SNAKE event tokens (CLIENT_PORTAL_ERROR, APPOINTMENT_RESCHEDULED…)
    s = s.replace(/\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b/g, function (m) {
      return protect('<code class="tp-token">' + m + '</code>');
    });
    // ISO timestamps
    s = s.replace(/\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}[0-9:.]*Z?\b/g, function (m) {
      return protect('<span class="tp-time">' + m + '</span>');
    });
    // clock times: 3:16 PM, 15:44:05 PM PT
    s = s.replace(/\b\d{1,2}:\d{2}(?::\d{2})?\s?(?:AM|PM|am|pm)?(?:\s?(?:P[SD]?T|E[SD]?T|UTC|GMT))?\b/g, function (m) {
      // bare "3:44" with no meridiem/zone is often a ratio or verse — only
      // highlight when it has AM/PM, a zone, or seconds
      if (!/(AM|PM|am|pm|P[SD]?T|E[SD]?T|UTC|GMT|\d:\d{2}:\d{2})/.test(m)) return m;
      return protect('<span class="tp-time">' + m + '</span>');
    });
    // quoted speech — the load-bearing bits of a Becky ticket. esc() leaves
    // double quotes alone (text nodes don't need them escaped), so match raw.
    s = s.replace(/"([^"\n]{2,400})"/g, function (_, body) {
      return protect('<span class="tp-quote">&ldquo;' + body + '&rdquo;</span>');
    });
    // restore protected spans
    s = s.replace(/\x00(\d+)\x00/g, function (_, i) { return stash[+i]; });
    return s;
  }

  // ---------------------------------------------------- dense question split --
  // A worker's `wt block --question` is often one wall of text: context, then
  // inline options "(a) … (b) … (c) …", then a follow-up question and a
  // recommendation. Lift that structure out so a human can scan it (CCC-64).
  // Returns HTML, or null when the text has no inline options to lift.
  // Labels may be "(a)", "(A)", "(1)" or carry a short note such as
  // "(A, recommended)" (a real ticket); the note is shown bold after the key.
  function renderDense(text) {
    var flat = String(text || '').replace(/\s+/g, ' ').trim();
    var marks = [];
    var re = /\(([a-zA-Z]|[1-9])(?:,\s*([^()]{1,30}))?\)\s/g;
    var m, kind = '';
    while ((m = re.exec(flat))) {
      var key = m[1].toLowerCase();
      var k = marks.length;
      var mKind = /\d/.test(key) ? 'num' : 'alpha';
      if (k && mKind !== kind) continue;
      var want = mKind === 'num' ? String(k + 1) : String.fromCharCode(97 + k);
      if (key !== want) continue;
      kind = mKind;
      marks.push({ at: m.index, end: re.lastIndex, key: m[1], note: (m[2] || '').trim() });
    }
    if (marks.length < 2) return null;
    var out = [];
    var lead = flat.slice(0, marks[0].at).trim();
    if (lead) out.push('<p class="tp-p">' + decorate(esc(lead)) + '</p>');
    var items = [];
    var tail = '';
    for (var k = 0; k < marks.length; k++) {
      var body = flat.slice(marks[k].end, k + 1 < marks.length ? marks[k + 1].at : flat.length).trim();
      if (k + 1 === marks.length) {
        // the last option swallows whatever follows it; cut at the first
        // sentence break so the follow-up question gets its own paragraph
        var cut = body.search(/[.?!]\s+(?=[A-Z])/);
        if (cut !== -1) { tail = body.slice(cut + 1).trim(); body = body.slice(0, cut + 1); }
      }
      var note = marks[k].note;
      items.push('<li' + (/recommend/i.test(note) ? ' class="tp-opt-rec"' : '') + '><span class="tp-opt-k">' + esc(marks[k].key) + '</span>'
        + (note ? '<strong class="tp-opt-note">' + esc(note) + '</strong> ' : '')
        + decorate(esc(body.replace(/[,;]\s*$/, ''))) + '</li>');
    }
    out.push('<ol class="tp-opts">' + items.join('') + '</ol>');
    if (tail) {
      // "My recommendation: …" is the part a busy reader wants first
      var rec = tail.search(/\b(?:My )?recommendation:/i);
      var before = rec === -1 ? tail : tail.slice(0, rec).trim();
      var recText = rec === -1 ? '' : tail.slice(rec).trim();
      if (before) out.push('<p class="tp-p">' + decorate(esc(before)) + '</p>');
      if (recText) out.push('<p class="tp-p tp-rec">' + decorate(esc(recText)) + '</p>');
    }
    return out.join('');
  }

  // Blocked notes are often a single paragraph ending in the actual ask.
  // Rearrange explicit source boundaries only; never synthesize a decision.
  function renderBlocked(text) {
    var original = String(text || '').trim();
    if (!original) return '';
    var tag = original.match(/^\[Q\d+(?::[^\]]+)?\]\s*/);
    var source = tag ? original.slice(tag[0].length) : original;
    var ask = '';
    var question = source.match(/(?:^|[.!]\s+|\n+)((?:Approve|Choose|Should|Can|Could|Would|Do|Which|What|How|May|Is|Are)\b[^\n]*\?)\s*$/i);
    if (question) {
      ask = question[1];
      source = source.slice(0, question.index).trim() + (/^[.!]/.test(question[0]) ? question[0][0] : '');
    }
    var parts = source.split(/\b(Recommended|Recommendation|Alternatives|Tradeoff|Trade-off|Risk|Impact):\s*/i);
    var sections = [];
    for (var i = 1; i < parts.length; i += 2) {
      sections.push({label: /recommend/i.test(parts[i]) ? 'Recommended approach' : parts[i], text: parts[i + 1].trim()});
    }
    function prose(value) {
      // Sentence breaks add breathing room without changing words or numbers.
      return render(value.replace(/;\s*(?=\([A-Z](?:\+[A-Z])*\)\s)/g, '\n\n')
        .replace(/([.!?]) +(?=[A-Z][a-z])/g, '$1\n\n'));
    }
    var html = '<section class="tp-blocked" aria-label="Blocked message" data-block-source="' + escAttr(original) + '">'
      + '<div class="tp-reading" aria-live="polite"><button type="button" class="tp-explain">Explain in plain English</button></div>'
      + '<div class="tp-blocked-source">';
    html += '<h3 class="tp-blocked-title">' + (ask ? 'The decision' : 'What’s blocking progress') + '</h3>';
    if (ask) html += '<div class="tp-blocked-ask">' + decorate(esc(ask)) + '</div>';
    if (tag) html += '<div class="tp-blocked-tag">' + esc(tag[0].trim()) + '</div>';
    sections.filter(function (s) { return s.label === 'Recommended approach'; }).forEach(function (s) {
      html += '<section class="tp-blocked-recommend"><h4>' + esc(s.label) + '</h4>' + prose(s.text) + '</section>';
    });
    if (parts[0].trim()) html += '<section><h4>' + (ask ? 'Why it paused' : 'Context') + '</h4>' + prose(parts[0].trim()) + '</section>';
    sections.filter(function (s) { return s.label !== 'Recommended approach'; }).forEach(function (s) {
      html += '<section><h4>' + esc(s.label) + '</h4>' + prose(s.text) + '</section>';
    });
    html += '<details class="tp-blocked-original"><summary>Original message</summary>' + render(original) + '</details></div></section>';
    return html;
  }

  function renderReading(reading, source) {
    var option = String(source || '').match(/\bRecommend(?:ed|ation):\s*\(([A-Z](?:\+[A-Z])*)\)/i);
    function line(tag, cls, text) { return '<' + tag + ' class="' + cls + '">' + esc(text) + '</' + tag + '>'; }
    var html = line('h3', 'tp-reading-question', reading.question)
      + '<section><h4>The proposal' + (option ? ' (' + esc(option[1]) + ')' : '') + '</h4>' + line('p', '', reading.proposal) + '</section>'
      + '<section class="tp-reading-consequence"><h4>What changes for you</h4>' + line('p', '', reading.consequence) + '</section>'
      + '<section><h4>Why it paused</h4>' + line('p', '', reading.context) + '</section>';
    if (Array.isArray(reading.alternatives) && reading.alternatives.length) {
      html += '<section><h4>Other options</h4><ul class="tp-reading-options">'
        + reading.alternatives.map(function (option) {
          return '<li><strong>' + esc(option.label) + '</strong><p>' + esc(option.explanation) + '</p></li>';
        }).join('') + '</ul></section>';
    }
    return html + '<p class="tp-reading-note">AI explanation · Check the original for exact wording.</p>';
  }

  function hydrateBlocked(root, currentQuestion) {
    var cards = Array.from(root.querySelectorAll('.tp-blocked'));
    cards.forEach(function (card) {
      var button = card.querySelector('.tp-explain');
      var slot = card.querySelector('.tp-reading');
      var source = card.getAttribute('data-block-source');
      var busy = false;
      async function explain() {
        if (busy || !card.isConnected) return;
        busy = true;
        button.disabled = true;
        button.textContent = 'Making this easier to read…';
        var abort = new AbortController();
        var timer = setTimeout(function () { abort.abort(); }, 65000);
        try {
          var response = await fetch('/api/queue/explain-block', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text: source}), signal: abort.signal
          });
          var data = await response.json();
          if (!response.ok || !data.ok || !data.reading) throw new Error(data.error || 'Explanation unavailable.');
          if (!card.isConnected) return;
          slot.innerHTML = renderReading(data.reading, source);
          var sourceView = card.querySelector('.tp-blocked-source');
          var details = document.createElement('details');
          details.className = 'tp-reading-source';
          var summary = document.createElement('summary');
          summary.textContent = 'Read the original & technical details';
          details.appendChild(summary);
          sourceView.replaceWith(details);
          details.appendChild(sourceView);
          var tag = source.match(/^\[Q\d+(?::[^\]]+)?\]/);
          if (tag) {
            var tagLine = document.createElement('p');
            tagLine.className = 'tp-blocked-tag';
            tagLine.textContent = tag[0];
            slot.insertBefore(tagLine, slot.querySelector('section'));
          }
          if (source === currentQuestion) {
            var reply = document.createElement('button');
            reply.type = 'button'; reply.className = 'tp-reading-reply';
            reply.textContent = 'Write your reply';
            reply.addEventListener('click', function () {
              var input = root.querySelector('.uxq-answer-input');
              if (input) { input.scrollIntoView({block: 'center', behavior: 'smooth'}); input.focus({preventScroll: true}); }
            });
            if (root.querySelector('.uxq-answer-input')) {
              slot.insertBefore(reply, slot.querySelector('section'));
              var hint = document.createElement('p');
              hint.className = 'tp-reply-hint';
              hint.textContent = /\bApprove\b[^?]*\?\s*$/i.test(source)
                ? 'Reply yes or no, or suggest another option.'
                : 'Reply with your choice or a short answer.';
              slot.insertBefore(hint, slot.querySelector('section'));
            }
          }
        } catch (error) {
          if (!card.isConnected) return;
          button.disabled = false; button.textContent = 'Try plain English again';
          var message = slot.querySelector('.tp-reading-error') || document.createElement('p');
          message.className = 'tp-reading-error';
          message.textContent = 'The explanation isn’t available right now. You can still read the full message below.';
          slot.appendChild(message);
        } finally { clearTimeout(timer); busy = false; }
      }
      button.addEventListener('click', explain);
      // Historical notes stay opt-in; only the current block gets one automatic
      // explanation when brought into view. No calls from a hidden timeline.
      if (source === currentQuestion && typeof IntersectionObserver !== 'undefined') {
        var observer = new IntersectionObserver(function (entries) {
          if (!root.isConnected) { observer.disconnect(); return; }
          if (entries.some(function (entry) { return entry.isIntersecting; })) {
            observer.disconnect(); explain();
          }
        });
        observer.observe(card);
        // Disconnect when the modal closes, even if this note was never seen.
        var removal = new MutationObserver(function () {
          if (!root.isConnected) { observer.disconnect(); removal.disconnect(); }
        });
        removal.observe(root.parentNode, {childList: true});
      }
    });
  }

  // ------------------------------------------------------------ body render --
  // Turns a raw ticket body into readable HTML: hidden machine comments, a
  // key/value meta strip (Studio: / Evidence time: …), then paragraphs,
  // headings, lists and code fences with inline decoration.
  function render(text) {
    var raw = String(text || '').replace(/\r\n/g, '\n');
    var findingId = '';
    raw = raw.replace(/<!--\s*digest-finding-id:\s*([a-f0-9]+)\s*-->/gi, function (_, id) {
      findingId = id;
      return '';
    });
    raw = raw.replace(/<!--[\s\S]*?-->/g, '');
    var lines = raw.split('\n');

    // Leading "Key: value" meta block (stops at first blank or non-matching line)
    var meta = [];
    var start = 0;
    while (start < lines.length && !lines[start].trim()) start++;
    while (start < lines.length) {
      var m = lines[start].match(/^([A-Z][A-Za-z0-9 _/-]{0,28}):\s+(.+)$/);
      if (!m) break;
      meta.push([m[1], m[2]]);
      start++;
    }
    if (findingId) meta.push(['Finding', findingId]);

    var out = [];
    if (meta.length) {
      out.push('<div class="tp-meta">' + meta.map(function (kv) {
        return '<span class="tp-meta-item"><span class="tp-meta-k">' + esc(kv[0])
          + '</span><span class="tp-meta-v">' + decorate(esc(kv[1])) + '</span></span>';
      }).join('') + '</div>');
    }

    var i = start;
    var para = [];
    function flushPara() {
      if (!para.length) return;
      var dense = renderDense(para.join(' '));
      if (dense) { out.push(dense); para = []; return; }
      out.push('<p class="tp-p">' + decorate(esc(para.join('\n'))).replace(/\n/g, '<br>') + '</p>');
      para = [];
    }
    while (i < lines.length) {
      var line = lines[i];
      var trimmed = line.trim();
      if (!trimmed) { flushPara(); i++; continue; }
      if (/^```/.test(trimmed)) {
        flushPara();
        var code = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i].trim())) { code.push(lines[i]); i++; }
        i++; // closing fence
        out.push('<pre class="tp-fence">' + esc(code.join('\n')) + '</pre>');
        continue;
      }
      var h = trimmed.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        flushPara();
        var lvl = Math.min(h[1].length + 2, 6);
        out.push('<h' + lvl + ' class="tp-h">' + decorate(esc(h[2])) + '</h' + lvl + '>');
        i++;
        continue;
      }
      if (trimmed.charAt(0) === '|') {
        flushPara();
        var trows = [];
        while (i < lines.length && lines[i].trim().charAt(0) === '|') {
          trows.push(lines[i].trim());
          i++;
        }
        var cells = trows
          .filter(function (r) { return !/^\|[\s\-:|]+\|?$/.test(r); }) // drop |---|---| separators
          .map(function (r) {
            return r.replace(/^\||\|$/g, '').split('|').map(function (c) {
              return '<td>' + decorate(esc(c.trim())) + '</td>';
            }).join('');
          });
        out.push('<table class="tp-table">' + cells.map(function (r) {
          return '<tr>' + r + '</tr>';
        }).join('') + '</table>');
        continue;
      }
      if (/^>\s?/.test(trimmed)) {
        flushPara();
        var qlines = [];
        while (i < lines.length && /^>\s?/.test(lines[i].trim())) {
          qlines.push(lines[i].trim().replace(/^>\s?/, ''));
          i++;
        }
        out.push('<blockquote class="tp-bq">' + decorate(esc(qlines.join('\n'))).replace(/\n/g, '<br>') + '</blockquote>');
        continue;
      }
      if (/^([-*]|\d+[.)])\s+/.test(trimmed)) {
        flushPara();
        var items = [];
        while (i < lines.length) {
          var lt = lines[i].trim();
          var lm = lt.match(/^([-*]|\d+[.)])\s+(.*)$/);
          if (!lm) break;
          items.push('<li>' + decorate(esc(lm[2])) + '</li>');
          i++;
        }
        out.push('<ul class="tp-ul">' + items.join('') + '</ul>');
        continue;
      }
      para.push(trimmed);
      i++;
    }
    flushPara();
    return '<div class="tp-body">' + out.join('') + '</div>';
  }

  // ------------------------------------------------------------- transcript --
  var ROLE_LABEL = { client: 'Client', becky: 'Becky', owner: 'Owner', system: 'System', tool: 'Tool' };
  function fmtWhen(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d)) return '';
    return d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }
  // data: {ok, key, kind, org, client, turns:[{role,text,at,meta}], truncated}
  function renderTranscript(data) {
    if (!data || !Array.isArray(data.turns)) return '';
    var head = [];
    if (data.org && data.org.name) head.push(esc(data.org.name));
    if (data.client && (data.client.name || data.client.phone)) {
      head.push(esc(data.client.name || data.client.phone));
    }
    if (data.kind) head.push(esc(data.kind) + ' conversation');
    var html = ['<div class="tp-conv">'];
    if (head.length) {
      html.push('<div class="tp-conv-head">' + head.join('<span class="tp-conv-sep">·</span>')
        + (data.key ? '<span class="tp-conv-key" title="' + escAttr(data.key) + '">' + esc(String(data.key).slice(0, 60)) + '</span>' : '')
        + '</div>');
    }
    if (data.truncated) {
      html.push('<div class="tp-conv-note">Older messages truncated — showing the most recent turns.</div>');
    }
    for (var i = 0; i < data.turns.length; i++) {
      var t = data.turns[i] || {};
      var role = String(t.role || 'system');
      var label = ROLE_LABEL[role] || role;
      var side = (role === 'becky') ? 'is-right' : (role === 'system' || role === 'tool') ? 'is-center' : 'is-left';
      var when = fmtWhen(t.at);
      html.push('<div class="tp-msg ' + side + ' is-' + escAttr(role) + '">'
        + '<div class="tp-msg-head"><span class="tp-msg-role">' + esc(label) + '</span>'
        + (when ? '<span class="tp-msg-time" title="' + escAttr(t.at) + '">' + esc(when) + '</span>' : '')
        + '</div>'
        + '<div class="tp-msg-body">' + decorate(esc(String(t.text || ''))).replace(/\n/g, '<br>') + '</div>'
        + '</div>');
    }
    html.push('</div>');
    return html.join('');
  }

  window.CCCTicketProse = {
    esc: esc,
    cleanForTitle: cleanForTitle,
    render: render,
    renderBlocked: renderBlocked,
    renderReading: renderReading,
    hydrateBlocked: hydrateBlocked,
    renderTranscript: renderTranscript,
  };
})();
