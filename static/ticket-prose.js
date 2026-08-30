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
    renderTranscript: renderTranscript,
  };
})();
