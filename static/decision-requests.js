/* Canonical global requests. Answer mutations require opt-in and explicit actions. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('requestsSection')) return;
  let rows = [], page = 0, busy = false, answersEnabled = false, sourceCurrent = false;
  const draftRevisions = new Map();
  const drafts = new Map(), submissions = new Map(), active = new Set(), notices = new Map();
  const storagePrefix = 'ccc:decision-answer:v1:';
  const historyPrefix = 'ccc:decision-answer-history:v1:';
  const rejectedHistory = new Map();
  // Persist before POST: an uncertain response must never create a second payload/key.
  let storageReady = true;
  try {
    for (let i = 0; i < sessionStorage.length; i++) {
      const key = sessionStorage.key(i);
      if (key.startsWith(historyPrefix)) {
        const history = JSON.parse(sessionStorage.getItem(key));
        if (!Array.isArray(history.answers) || typeof history.replaces_answer_id !== 'string') throw new Error('Invalid rejection history');
        rejectedHistory.set(key.slice(historyPrefix.length), history);
        continue;
      }
      if (!key.startsWith(storagePrefix)) continue;
      const entry = JSON.parse(sessionStorage.getItem(key));
      const p = entry && entry.payload;
      if (!p || typeof p.request_id !== 'string' || key !== storagePrefix + p.request_id
          || typeof p.text !== 'string' || (!entry.saved && typeof p.idempotency_key !== 'string')
          || !Number.isSafeInteger(p.expected_revision)) throw new Error('Invalid saved submission');
      submissions.set(p.request_id, entry);
    }
  } catch (_) { storageReady = false; }
  function persist(id, entry) {
    try { sessionStorage.setItem(storagePrefix + id, JSON.stringify(entry)); return true; }
    catch (_) { return false; }
  }
  function answerFor(row) {
    const entry = submissions.get(row.request_id);
    if (entry && entry.saved) return entry.saved;
    const history = rejectedHistory.get(row.request_id);
    if (row.answer && history && row.answer.answer_id === history.replaces_answer_id && row.answer.state === 'rejected') return null;
    return row.answer;
  }
  function absorb(row) {
    const entry = submissions.get(row.request_id), saved = row.answer;
    if (!entry || !saved || active.has(row.request_id)) return;
    if (entry.saved?.answer_id === saved.answer_id && entry.saved.state === 'accepted' && saved.state === 'accepted' && saved.session_consumed === true) {
      entry.saved.session_consumed = true; persist(row.request_id, entry);
    }
    // A GET started before an explicit action may finish after its receipt.
    // Keep that receipt terminal and never substitute another answer identity.
    if (entry.saved && (entry.saved.answer_id !== saved.answer_id
        || ['accepted', 'rejected'].includes(entry.saved.state))) return;
    if (rejectedHistory.get(row.request_id)?.replaces_answer_id === saved.answer_id) return;
    if (saved.text === entry.payload.text && saved.expected_revision === entry.payload.expected_revision) {
      // A lost Send response remains uncertain until an explicit receipt check.
      if (!entry.deliveryUnknown || saved.state === 'accepted' || saved.state === 'rejected') {
        entry.saved = saved; entry.deliveryUnknown = false; notices.delete(row.request_id); persist(row.request_id, entry);
      }
    }
  }
  function newKey() {
    if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('');
    return hex.slice(0, 8) + '-' + hex.slice(8, 12) + '-' + hex.slice(12, 16) + '-' + hex.slice(16, 20) + '-' + hex.slice(20);
  }
  function supported(row) { return row?.adapter === 'watchtower-block' || (row?.adapter === 'session-question' && row.reply_supported === true); }
  function currentSource(row) { return row?.source_current === true || sourceCurrent; }
  function startNewAnswer(row) {
    const id = row.request_id, saved = answerFor(row);
    if (!answersEnabled || !currentSource(row) || row.can_answer !== true || active.has(id) || saved?.state !== 'rejected') return;
    const previous = rejectedHistory.get(id)?.answers || [];
    const history = {answers:[...previous, saved], replaces_answer_id:saved.answer_id};
    try {
      sessionStorage.setItem(historyPrefix + id, JSON.stringify(history));
      sessionStorage.removeItem(storagePrefix + id);
    } catch (_) { notices.set(id, 'Cannot retain rejection history. The saved answer has not been reset.'); render(); return; }
    rejectedHistory.set(id, history); submissions.delete(id); drafts.delete(id); draftRevisions.delete(id); notices.delete(id); render();
  }
  function controls(row, cell) {
    const id = row.request_id, entry = submissions.get(id), saved = answerFor(row);
    for (const rejected of rejectedHistory.get(id)?.answers || []) {
      cell.append(text('span', 'Previous answer · rejected', 'request-meta'), text('div', rejected.text, 'request-frozen request-rejected-history'));
    }
    if (saved) {
      cell.append(text('span', 'Saved answer · ' + saved.state, 'request-meta'), text('div', saved.text, 'request-frozen'));
      if (saved.state === 'accepted') cell.append(text('span', 'Acceptance does not mean the work is complete.', 'request-meta'));
      if (saved.state === 'rejected') cell.append(text('span', 'Answer definitively rejected. Exact text is retained. Check source before deciding on a new answer.', 'request-meta request-error'));
    }
    if (entry && !entry.saved) {
      cell.append(text('span', 'Frozen submission · save not confirmed', 'request-meta'), text('div', entry.payload.text, 'request-frozen'),
        text('span', 'Check source to recover a saved answer. If saving is still unconfirmed, retry the exact same submission. A source conflict requires checking the current question; this text will not be replaced.', 'request-meta'));
    }
    const notice = notices.get(id);
    if (notice) { const line = text('div', notice, 'request-meta request-error'); line.setAttribute('role', 'status'); cell.append(line); }
    if (!answersEnabled || !supported(row) || row.kind !== 'question' || row.delivery_owner === 'routine') return;
    function button(label, action) {
      const b = text('button', label); b.type = 'button'; b.disabled = active.has(id);
      b.onclick = action; cell.append(b);
    }
    if (entry && !entry.saved) {
      if (!row.answer || row.answer.answer_id === rejectedHistory.get(id)?.replaces_answer_id) button('Retry saving same answer', () => mutate(row, 'record'));
      return;
    }
    if (saved) {
      if (saved.state === 'rejected' && row.can_answer === true && currentSource(row)) button('Start new answer', () => startNewAnswer(row));
      if (saved.state === 'pending') button('Send saved answer', () => mutate(row, 'send'));
      if (saved.state === 'sending' || saved.state === 'unknown') button('Check delivery', () => mutate(row, 'reconcile'));
      return;
    }
    if (row.can_answer !== true || !currentSource(row)) return;
    const form = document.createElement('form'); form.className = 'request-answer';
    const label = text('label', 'Your answer');
    const input = document.createElement('textarea'); input.rows = 3; input.maxLength = 24000;
    input.dataset.answerDraft = id; input.value = drafts.get(id) || ''; input.disabled = active.has(id);
    label.append(input); form.append(label);
    form.append(text('span', 'Save records this exact text only. Send is a separate action.', 'request-meta'));
    const save = text('button', 'Save answer'); save.type = 'submit';
    const questionChanged = () => draftRevisions.has(id) && draftRevisions.get(id) !== row.revision;
    save.disabled = !storageReady || !input.value.trim() || active.has(id) || questionChanged();
    input.oninput = () => {
      if (!draftRevisions.has(id)) draftRevisions.set(id, row.revision);
      drafts.set(id, input.value);
      save.disabled = !storageReady || !input.value.trim() || active.has(id) || questionChanged();
    };
    if (questionChanged()) {
      const warning = text('div', 'The question changed while you were drafting. Your text is retained. Review the current question above before using this draft.', 'request-meta request-error');
      warning.setAttribute('role', 'status'); form.append(warning);
      const review = text('button', 'Use draft for current question'); review.type = 'button'; review.disabled = active.has(id);
      review.onclick = () => {
        const current = rows.find(candidate => candidate.request_id === id);
        if (!answersEnabled || !currentSource(current) || current?.can_answer !== true || active.has(id) || answerFor(current)) return;
        draftRevisions.set(id, current.revision); render();
      };
      form.append(review);
    }
    form.onsubmit = event => { event.preventDefault(); mutate(row, 'record'); };
    form.append(save);
    if (!storageReady) form.append(text('span', 'Session storage is unavailable or unreadable. Saving is blocked to protect recovery after a lost response.', 'request-meta request-error'));
    cell.append(form);
  }
  async function mutate(row, action) {
    const id = row.request_id;
    if (!answersEnabled || active.has(id)) return;
    let entry = submissions.get(id);
    if (action === 'record' && !entry) {
      if (!currentSource(row) || row.can_answer !== true || !storageReady) return;
      const draft = drafts.get(id) || '';
      const draftRevision = draftRevisions.get(id);
      if (!draft.trim() || draftRevision !== row.revision) return;
      entry = {payload:{request_id:id, expected_revision:draftRevision, text:draft, idempotency_key:newKey()}};
      if (!persist(id, entry)) {
        notices.set(id, 'Cannot retain this submission in session storage. Nothing was sent; keep your draft and restore session storage before saving.'); render(); return;
      }
      submissions.set(id, entry);
    }
    const saved = answerFor(row);
    if (action !== 'record') {
      if (!saved || !(action === 'send' ? saved.state === 'pending' : ['sending', 'unknown'].includes(saved.state))) return;
      entry = entry || {payload:{request_id:id, expected_revision:saved.expected_revision, text:saved.text}, saved};
      // Treat an interrupted send conservatively, including across reloads.
      if (action === 'send') { entry.saved = {...saved, state:'unknown'}; entry.deliveryUnknown = true; }
      if (!persist(id, entry)) { notices.set(id, 'Cannot retain delivery recovery state. Nothing was sent.'); render(); return; }
      submissions.set(id, entry);
    }
    active.add(id); notices.delete(id); render();
    try {
      const response = await fetch('/api/decision-inbox/answers' + (action === 'record' ? '' : '/' + action), {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify(action === 'record' ? entry.payload : {answer_id:saved.answer_id})
      });
      const data = await response.json();
      if (!response.ok || data.ok !== true) throw new Error(data.error || 'Response unavailable');
      if (data.request_id !== id || !data.answer || data.answer.text !== entry.payload.text
          || data.answer.expected_revision !== entry.payload.expected_revision
          || !['pending','sending','unknown','accepted','rejected'].includes(data.state) || !data.answer_id
          || (action !== 'record' && data.answer_id !== saved.answer_id)) throw new Error('Unrecognized answer response');
      entry.saved = {answer_id:data.answer_id, state:data.state, text:data.answer.text, expected_revision:data.answer.expected_revision};
      entry.deliveryUnknown = false; persist(id, entry);
    } catch (error) {
      notices.set(id, (action === 'record' ? 'Save not confirmed. ' : 'Delivery not confirmed. Use Check delivery; do not resend. ')
        + error.message + '. Check source for current state.');
    } finally { active.delete(id); render(); }
  }
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
    if (answerFor(row)) return ['accepted','rejected'].includes(answerFor(row).state) ? 'history' : 'delivery';
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
    const focused = document.activeElement;
    const focusId = focused && focused.dataset.answerDraft;
    const selection = focusId ? [focused.selectionStart, focused.selectionEnd, focused.scrollTop] : null;
    const selected = rows.filter(row => (filter === 'all' || bucket(row) === filter || (filter === 'needs' && submissions.has(row.request_id)))
      && [row.title, row.body, row.origin_id, row.request_id, ...(row.aliases || [])].join(' ').toLowerCase().includes(query));
    const total = selected.length;
    page = Math.max(0, Math.min(page, Math.max(0, Math.ceil(total / limit) - 1)));
    const body = $('requestRows'); body.replaceChildren();
    selected.slice(page * limit, (page + 1) * limit).forEach(row => {
      const tr = document.createElement('tr'); tr.dataset.requestId = row.request_id;
      const question = document.createElement('td'), state = document.createElement('td');
      if (row.adapter === 'watchtower-block') {
        const origin = text('a', row.origin_id, 'request-origin');
        origin.href = '/?ticket=' + encodeURIComponent(row.origin_id);
        origin.target = '_top'; origin.title = 'Open ticket ' + row.origin_id;
        origin.onclick = event => {
          if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || window.parent === window) return;
          try {
            if (window.parent.location.origin !== location.origin) return;
          } catch (_) { return; }
          event.preventDefault();
          window.parent.postMessage({type:'ccc:open-ticket', ref:row.origin_id}, location.origin);
        };
        question.append(origin);
      }
      question.append(text('strong', row.title), text('div', row.body, 'request-body'));
      const aliases = (row.aliases || []).filter(alias => /^\[Q\d+:|^D-\d+$/.test(alias));
      const references = row.adapter === 'watchtower-block' ? aliases : [row.origin_id, ...aliases];
      if (references.length) question.append(text('span', references.join(' · '), 'request-meta'));
      const identity = text('a', 'Request ' + row.request_id.slice(0, 8), 'request-meta');
      identity.href = '#request-' + row.request_id; identity.title = row.request_id;
      tr.id = 'request-' + row.request_id; question.append(identity);
      const saved = answerFor(row);
      state.append(text('span', saved ? ({pending:'Answer saved; delivery pending', sending:'Checking answer delivery', unknown:'Answer delivery unknown', accepted:row.adapter === 'session-question' ? (saved.session_consumed ? 'Answer received by session' : 'Answer saved for session; awaiting receipt') : 'Answer accepted by source', rejected:'Answer rejected by source'})[saved.state] : stateLabel(row), 'request-state'));
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
      if (row.delivery_state === 'accepted' && !saved) state.append(text('span', 'Acceptance does not mean the work is complete.', 'request-meta'));
      controls(row, question);
      tr.append(question, state); body.append(tr);
    });
    if (!total) {
      const td = text('td', 'No saved requests match this view. Source coverage is shown above.');
      td.colSpan = 2; const tr = document.createElement('tr'); tr.append(td); body.append(tr);
    }
    $('requestPage').textContent = total ? (page * limit + 1) + '–' + Math.min((page + 1) * limit, total) + ' of ' + total : '0 matching requests';
    if (focusId) {
      const replacement = [...body.querySelectorAll('textarea')].find(input => input.dataset.answerDraft === focusId);
      if (replacement) { replacement.focus({preventScroll:true}); replacement.setSelectionRange(selection[0], selection[1]); replacement.scrollTop = selection[2]; }
    }
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
      rows = data.requests; answersEnabled = data.answers_enabled === true; sourceCurrent = data.source_current === true;
      rows.forEach(absorb);
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
      sourceCurrent = false;
      rows = rows.map(row => ({...row, source_current:false, can_answer:false}));
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
