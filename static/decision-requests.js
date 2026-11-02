/* Canonical global requests. This surface never sends an answer or runs a scan. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('requestsSection')) return;
  let rows = [], page = 0, busy = false;
  const limit = 10;
  const time = new Intl.DateTimeFormat('en-US', {timeZone:'America/Los_Angeles', year:'numeric',
    month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit', timeZoneName:'short'});
  const text = (tag, value, cls) => {
    const element = document.createElement(tag);
    element.textContent = value;
    if (cls) element.className = cls;
    return element;
  };
  function bucket(row) {
    if (row.needs_answer) return 'needs';
    if (row.status === 'open' && row.answer_state === 'recorded') return 'delivery';
    return 'history';
  }
  function stateLabel(row) {
    if (row.status !== 'open') return row.status === 'withdrawn' ? 'No longer waiting at source' : 'Answered locally';
    if (row.needs_answer) return row.delivery_state === 'rejected' ? 'Needs answer · previous answer rejected' : 'Needs answer';
    if (row.delivery_state === 'accepted') return 'Answer accepted by source';
    if (row.delivery_state === 'unknown' || row.delivery_state === 'sending') return 'Checking answer delivery';
    if (row.delivery_state === 'pending') return 'Answer saved; delivery pending';
    if (row.delivery_state === 'rejected') return 'Answer rejected by source';
    if (row.kind !== 'question') return 'Read-only approval';
    return row.delivery_state;
  }
  function render() {
    const query = $('requestSearch').value.trim().toLowerCase(), filter = $('requestFilter').value;
    const selected = rows.filter(row => (filter === 'all' || bucket(row) === filter)
      && [row.title, row.body, row.origin_id, row.request_id, ...(row.aliases || [])].join(' ').toLowerCase().includes(query));
    const total = selected.length;
    page = Math.max(0, Math.min(page, Math.max(0, Math.ceil(total / limit) - 1)));
    const body = $('requestRows'); body.replaceChildren();
    selected.slice(page * limit, (page + 1) * limit).forEach(row => {
      const tr = document.createElement('tr'); tr.dataset.requestId = row.request_id;
      const question = document.createElement('td'), state = document.createElement('td');
      question.append(text('strong', row.title), text('div', row.body, 'request-body'));
      const aliases = (row.aliases || []).filter(alias => /^\[Q\d+:|^D-\d+$/.test(alias));
      question.append(text('span', [row.origin_id, ...aliases].join(' · '), 'request-meta'));
      const identity = text('a', 'Request ' + row.request_id.slice(0, 8), 'request-meta');
      identity.href = '#request-' + row.request_id; identity.title = row.request_id;
      tr.id = 'request-' + row.request_id; question.append(identity);
      state.append(text('span', stateLabel(row), 'request-state'));
      state.append(text('span', row.adapter === 'session-question' ? 'Promoted from a session' : 'Local queue owner wait', 'request-meta'));
      const date = new Date(row.observed_at);
      if (Number.isFinite(date.getTime())) {
        const stamp = text('time', time.format(date), 'request-meta'); stamp.dateTime = row.observed_at;
        state.append(stamp);
        if (row.episode_provenance) state.append(text('span', 'Episode initialized at this time; historical block time is unknown.', 'request-meta'));
      }
      if (row.needs_answer) state.append(text('span', row.answer_unavailable_reason, 'request-meta'));
      if (row.session_id) {
        const link = text('a', 'Open session', 'request-meta');
        link.href = '/?session=' + encodeURIComponent(row.session_id); link.target = '_top'; state.append(link);
      }
      if (row.delivery_state === 'accepted') state.append(text('span', 'Acceptance does not mean the work is complete.', 'request-meta'));
      tr.append(question, state); body.append(tr);
    });
    if (!total) {
      const td = text('td', 'No saved requests match this view. Source coverage is shown above.');
      td.colSpan = 2; const tr = document.createElement('tr'); tr.append(td); body.append(tr);
    }
    $('requestPage').textContent = total ? (page * limit + 1) + '–' + Math.min((page + 1) * limit, total) + ' of ' + total : '0 matching requests';
    $('requestPrev').disabled = busy || page === 0;
    $('requestNext').disabled = busy || (page + 1) * limit >= total;
  }
  async function refresh() {
    if (busy) return;
    busy = true; $('requestRefresh').disabled = true; render();
    try {
      const response = await fetch('/api/decision-inbox/requests', {cache:'no-store'});
      const data = await response.json();
      if (!response.ok || !data.ok || !Array.isArray(data.requests)) throw new Error('Cannot read saved requests');
      rows = data.requests;
      const issues = $('requestCoverageIssues'); issues.replaceChildren();
      (data.coverage_issues || []).forEach(issue => {
        const line = document.createElement('div');
        line.append(text('strong', issue.origin_id + ': ' + issue.title), text('div', issue.body, 'request-body'), text('span', issue.reason, 'request-meta'));
        issues.append(line);
      });
      $('requestCount').textContent = data.source_current ? '(' + data.needs_answer_count + ' need an answer)'
        : (data.coverage_issues || []).length ? '(coverage incomplete)' : '(source unavailable)';
      $('requestsStatus').textContent = data.source_current
        ? 'Checked ' + time.format(new Date(data.checked_at)) + '. Covers local queue owner waits and explicitly promoted session questions. Interactive questions stay in their sessions; reviews and remote queues are outside this check.'
        : (data.error || 'The local queue source could not be checked.') + ' Showing previously observed questions; this is not a current count. Check the source again.';
      $('requestsStatus').classList.toggle('bad', !data.source_current);
      if (window.parent !== window) window.parent.postMessage({type:'ccc:decision-count', count:data.needs_answer_count}, location.origin);
    } catch (_) {
      $('requestCount').textContent = '(unavailable)';
      $('requestsStatus').textContent = 'Cannot refresh owner questions. Any displayed rows may be out of date. Check the source again.';
      $('requestsStatus').classList.add('bad');
      if (window.parent !== window) window.parent.postMessage({type:'ccc:decision-count', count:null}, location.origin);
    } finally { busy = false; $('requestRefresh').disabled = false; render(); }
  }
  $('requestSearch').addEventListener('input', () => { page = 0; render(); });
  $('requestFilter').addEventListener('change', () => { page = 0; render(); });
  $('requestPrev').onclick = () => { if (!busy) { page--; render(); } };
  $('requestNext').onclick = () => { if (!busy) { page++; render(); } };
  $('requestRefresh').onclick = refresh;
  function followHash() {
    const match = location.hash.match(/^#request-([a-f0-9-]{36})$/);
    if (!match) return;
    $('requestSearch').value = match[1]; $('requestFilter').value = 'all'; page = 0; render();
  }
  window.addEventListener('hashchange', followHash);
  followHash();
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 30000);
})();
