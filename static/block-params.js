// CCC-379: read-only "Block parameters" disclosure for a blocked WatchTower
// ticket. Lazy: nothing is fetched until the owner opens it. Shared by the
// ticket modal (index.html) and the decision cards (decision-guide.html).
(function () {
  function h(tag, text, cls) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null && text !== '') n.textContent = String(text);
    return n;
  }
  function row(label, value) {
    var r = h('div', '', 'bp-row');
    r.appendChild(h('span', label, 'bp-k'));
    r.appendChild(h('span', typeof value === 'object' ? JSON.stringify(value) : value, 'bp-v'));
    return r;
  }
  var FIELDS = ['class', 'weight', 'confidence', 'recommend', 'undo', 'major_because', 'major_detail'];
  function fill(body, d) {
    body.textContent = '';
    var p = d.proposal, g = d.gate;
    if (!p && !g) { body.appendChild(h('div', 'No structured proposal was recorded on this block (the worker blocked without --class/--weight).', 'bp-empty')); return; }
    if (p) {
      FIELDS.forEach(function (k) {
        var v = p[k];
        if (v !== undefined && v !== null && v !== '') body.appendChild(row(k.replace(/_/g, ' '), v));
      });
      Object.keys(p).forEach(function (k) {
        if (FIELDS.indexOf(k) >= 0 || k === 'question') return;
        var v = p[k];
        if (v === undefined || v === null || v === '' || (typeof v === 'object' && !Object.keys(v).length)) return;
        body.appendChild(row(k.replace(/_/g, ' '), v));
      });
    }
    if (g) {
      var head = h('div', '', 'bp-gate');
      head.appendChild(h('strong', g.approve ? 'Gate check: eligible' + (g.dry_run ? ' (test mode, not approved)' : '') : 'Gate check: not eligible'));
      if (g.policy_hash) head.appendChild(h('span', ' policy ' + g.policy_hash, 'bp-k'));
      body.appendChild(head);
      (g.checks || []).forEach(function (c) {
        var line = h('div', '', 'bp-check ' + (c.ok ? 'is-pass' : 'is-fail'));
        line.appendChild(h('span', c.ok ? 'PASS' : 'FAIL', 'bp-badge'));
        line.appendChild(h('span', c.name + (c.ok ? '' : ': ' + (c.detail || '')), 'bp-name'));
        if (!c.ok && c.fix) line.appendChild(h('div', 'fix: ' + c.fix, 'bp-fix'));
        body.appendChild(line);
      });
      (g.reasons || []).forEach(function (r) { body.appendChild(h('div', r, 'bp-reason')); });
      if (g.matched && g.matched.length) body.appendChild(row('matched', g.matched.join(', ')));
    }
  }
  window.cccBlockParams = function (ref) {
    if (!ref) return null;
    var det = h('details', '', 'bp-details');
    det.appendChild(h('summary', 'Block parameters', 'bp-summary'));
    var body = h('div', 'Loading…', 'bp-body');
    det.appendChild(body);
    var loaded = false;
    det.addEventListener('toggle', function () {
      if (!det.open || loaded) return;
      loaded = true;
      fetch('/api/queue/block-params?ref=' + encodeURIComponent(ref), { cache: 'no-store' })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (d) {
          if (!d || !d.ok) { loaded = false; body.textContent = 'Could not load: ' + ((d && d.error) || 'error'); return; }
          fill(body, d);
        })
        .catch(function (e) { loaded = false; body.textContent = 'Could not load: ' + ((e && e.message) || 'network error'); });
    });
    return det;
  };
  var css = document.createElement('style');
  css.textContent = '.bp-details{margin:8px 0;font-size:12.5px}.bp-summary{cursor:pointer;color:var(--text-muted,#8b949e)}'
    + '.bp-body{display:flex;flex-direction:column;gap:4px;padding:6px 0 2px}.bp-row{display:flex;gap:8px}'
    + '.bp-k{color:var(--text-muted,#8b949e);min-width:110px;flex-shrink:0;text-transform:capitalize}.bp-v{white-space:pre-wrap;overflow-wrap:anywhere}'
    + '.bp-gate{margin-top:6px}.bp-check{display:flex;gap:8px;flex-wrap:wrap}.bp-badge{font-weight:700;font-size:10.5px}'
    + '.is-pass .bp-badge{color:#3fb950}.is-fail .bp-badge{color:#f85149}.bp-fix,.bp-reason{color:var(--text-muted,#8b949e)}';
  document.head.appendChild(css);
})();
