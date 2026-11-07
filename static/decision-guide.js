/* Deterministic owner queue. Background refreshes only read source questions. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const key = 'ccc:decision-guide:v1';
  const states = ['pending', 'sending', 'unknown', 'accepted', 'rejected'];
  const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
  const revision = value => Number.isSafeInteger(value) && value > 0;
  let state = {entries:{}, drafts:{}, seen:[], snapshots:{}, selected:null, closes:{}, closed:{}};
  let storageReady = true, rows = [], model = null, loading = false, loaded = false, readError = '', active = null, confirming = null;
  const notices = new Map();
  document.documentElement.classList.toggle('embedded', parent !== window);
  function validAnswer(answer) {
    return answer && uuid(answer.answer_id) && states.includes(answer.state) && typeof answer.text === 'string' && revision(answer.expected_revision);
  }
  try {
    const stored = sessionStorage.getItem(key);
    if (stored) {
      const parsed = JSON.parse(stored);
      if (!parsed.entries || !parsed.drafts || !parsed.snapshots || !Array.isArray(parsed.seen)) throw Error('Invalid saved history');
      for (const [id, entry] of Object.entries(parsed.entries)) {
        const p = entry.payload;
        if (!uuid(id) || !p || p.request_id !== id || typeof p.text !== 'string' || !revision(p.expected_revision)
            || (p.idempotency_key !== undefined && !uuid(p.idempotency_key)) || (!entry.saved && !uuid(p.idempotency_key))
            || (entry.saved && (!validAnswer(entry.saved) || entry.saved.text !== p.text || entry.saved.expected_revision !== p.expected_revision))) throw Error('Invalid frozen answer');
      }
      for (const [id, draft] of Object.entries(parsed.drafts)) {
        if (!uuid(id) || typeof draft.text !== 'string' || !revision(draft.revision) || typeof draft.body !== 'string') throw Error('Invalid saved draft');
      }
      for (const [id, close] of Object.entries(parsed.closes || {})) {
        if (!uuid(id) || close.request_id !== id || !uuid(close.idempotency_key) || !revision(close.expected_revision)) throw Error('Invalid saved close');
      }
      state = {...state, ...parsed};
    }
  } catch (_) { storageReady = false; }
  function persist() {
    if (!storageReady) return false;
    try { sessionStorage.setItem(key, JSON.stringify(state)); return true; }
    catch (_) { storageReady = false; return false; }
  }
  function newKey() {
    if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map(b => b.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  }
  function el(tag, content, cls) {
    const node = document.createElement(tag); node.textContent = content;
    if (cls) node.className = cls;
    return node;
  }
  const time = new Intl.DateTimeFormat('en-US', {timeZone:'America/Los_Angeles', year:'numeric', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit', timeZoneName:'short'});
  function current() { return rows.find(row => row.request_id === state.selected) || state.snapshots[state.selected]; }
  function openRows() { return rows.filter(row => row.status === 'open' && row.kind === 'question' && !state.closed[row.request_id]
    && state.entries[row.request_id]?.saved?.state !== 'accepted' && row.answer?.state !== 'accepted'); }
  function supported(row) { return row?.adapter === 'watchtower-block' || row?.adapter === 'watchtower-github' || (row?.adapter === 'session-question' && row.reply_supported === true); }
  function currentSource(row) { return row?.source_current === true || model?.source_current === true; }
  function gate(row) {
    if (!storageReady) return 'Session storage is unavailable or unreadable. Answers are blocked to protect recovery.';
    if (!model || readError || !currentSource(row)) return 'Could not confirm this list is current. Use Try again before answering.';
    if (model.answers_enabled !== true) return 'Source answers are not enabled. This question is read-only.';
    if (!row || !rows.some(r => r.request_id === row.request_id) || row.status !== 'open') return 'This question is no longer open at source. This is not a delivery receipt.';
    if (row.can_answer !== true) return row.answer_unavailable_reason || 'The source does not allow an answer to this question.';
    if (!supported(row) || row.kind !== 'question' || row.delivery_owner === 'routine') return row.answer_unavailable_reason || 'Reply at the originating source; this question is read-only here.';
    if (!uuid(row.request_id) || !revision(row.revision)) return 'Source question identity is unavailable.';
    return '';
  }
  function frozenGate(action, row) {
    if (!storageReady) return 'Session storage is unavailable or unreadable. Recovery is blocked to protect the frozen answer.';
    if (model?.answers_enabled !== true) return 'Source answers are not enabled. This question is read-only.';
    // The immutable key is replayed before backend freshness checks. A save retry
    // never sends, and receipt lookup also remains safe without source coverage.
    if (action === 'record' || action === 'reconcile') return '';
    if (readError || !currentSource(row)) return 'Could not confirm this list is current. Use Try again before sending the saved answer.';
    if (!row || !supported(row) || row.kind !== 'question' || row.delivery_owner === 'routine') return row?.answer_unavailable_reason || 'This source does not support sending the saved answer here.';
    return '';
  }
  const definiteRefusals = ['conflict', 'answer_in_progress', 'routine_owned', 'not_found', 'unsupported', 'invalid_request', 'answers_disabled'];
  function closeGate(row) {
    if (!storageReady || !model || readError || !row || row.can_close !== true || state.closed[row.request_id] || state.closes[row.request_id]) return 'unavailable';
    if (state.entries[row.request_id] || !rows.some(r => r.request_id === row.request_id) || row.status !== 'open') return 'unavailable';
    return uuid(row.request_id) && revision(row.revision) ? '' : 'unavailable';
  }
  function closesTicket(row) { return row?.adapter === 'watchtower-block'; }
  function revealCurrent() {
    $('current').scrollIntoView({block:'start', behavior:'auto'});
  }
  function select(id, reveal = false) {
    state.selected = id;
    if (id && !state.seen.includes(id)) state.seen.push(id);
    const row = rows.find(r => r.request_id === id);
    if (row && !state.entries[id] && !state.drafts[id]) state.snapshots[id] = row;
    persist(); render();
    if (reveal) revealCurrent();
  }
  function next(reveal = true) {
    const available = openRows().filter(row => state.entries[row.request_id]?.saved?.state !== 'accepted' && row.answer?.state !== 'accepted');
    const index = available.findIndex(row => row.request_id === state.selected);
    select(available.length ? available[(index + 1) % available.length].request_id : null, reveal);
  }
  function absorb(row) {
    const saved = row.answer;
    if (!validAnswer(saved) || active === row.request_id) return;
    let entry = state.entries[row.request_id];
    if (entry?.saved?.answer_id === saved.answer_id && entry.saved.state === 'accepted' && saved.state === 'accepted' && saved.session_consumed === true) entry.saved.session_consumed = true;
    if (entry) {
      if (entry.saved && (entry.saved.answer_id !== saved.answer_id || ['accepted','rejected'].includes(entry.saved.state))) return;
      if (saved.text !== entry.payload.text || saved.expected_revision !== entry.payload.expected_revision) return;
      if (entry.deliveryUnknown && !['accepted','rejected'].includes(saved.state)) return;
    } else {
      entry = {payload:{request_id:row.request_id, expected_revision:saved.expected_revision, text:saved.text}};
      state.entries[row.request_id] = entry;
      state.snapshots[row.request_id] = row;
    }
    entry.saved = {...saved}; entry.deliveryUnknown = ['sending','unknown'].includes(saved.state);
  }
  function ticket(row) {
    if (row.adapter === 'session-question' && uuid(row.session_id)) { const link = el('a', 'Open originating session', 'ticket'); link.href = '/?session=' + encodeURIComponent(row.session_id); link.target = '_top'; return link; }
    if (row.adapter !== 'watchtower-block' && row.adapter !== 'watchtower-github') return el('span', row.origin_id || '', 'meta');
    const link = el('a', row.origin_id, 'ticket');
    link.href = '/?ticket=' + encodeURIComponent(row.origin_id); link.target = '_top'; link.title = 'Open ticket ' + row.origin_id;
    link.onclick = event => {
      if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || parent === window) return;
      try { if (parent.location.origin !== location.origin) return; } catch (_) { return; }
      event.preventDefault(); parent.postMessage({type:'ccc:open-ticket', ref:row.origin_id}, location.origin);
    };
    return link;
  }
  function status(entry) {
    if (active === entry.payload.request_id) return entry.saved
      ? 'Your answer is saved. Waiting for confirmation from the source…'
      : 'Saving your answer…';
    if (!entry.saved) return 'Save not confirmed. Retry saving the same frozen answer; nothing will be sent by that retry.';
    return {pending:'Answer saved. Delivery pending; sending requires an explicit click.', sending:'Delivery unknown. Check delivery; do not resend.', unknown:'Delivery unknown. Check delivery; do not resend.', accepted: (entry.saved.receipt?.acceptance === 'source_inbox' || state.snapshots[entry.payload.request_id]?.adapter === 'session-question') ? (entry.saved.session_consumed ? 'Answer received by the originating session. The work is not necessarily complete.' : 'Answer saved for the originating session; waiting for session receipt. The work is not complete.') : 'Answer accepted by source. Delivery confirmed; the work is not necessarily complete.', rejected:'Answer rejected by source. Your exact text is retained; this question has not advanced.'}[entry.saved.state];
  }
  function question(row, entry, history = false) {
    const card = el('article', '', history ? 'history' : 'question'); card.dataset.requestId = row.request_id;
    const weight = window.decisionWeight(row);
    card.append(ticket(row), el('p', `${weight.level}: ${weight.reason}`, 'weight weight-' + weight.level.toLowerCase()), el('h2', row.title || row.origin_id));
    if (row.aliases?.length) card.append(el('div', row.aliases.join(' · '), 'meta'));
    const date = new Date(row.observed_at);
    if (Number.isFinite(date.getTime())) {
      const stamp = el('time', time.format(date), 'meta'); stamp.dateTime = row.observed_at; card.append(stamp);
    }
    if (row.episode_provenance) card.append(el('p', 'Episode initialized at this time; historical block time is unknown.', 'meta'));
    card.append(el('div', row.body, 'raw'));
    if (state.closed[row.request_id]) card.append(el('p', state.closed[row.request_id], 'receipt accepted'));
    if (entry) {
      const reply = el('div', '', 'reply'); reply.append(el('span', 'Your answer', 'speaker'), el('span', entry.payload.text)); card.append(reply);
      card.append(el('p', status(entry), 'receipt ' + (entry.saved?.state || '')));
    }
    return card;
  }
  function recovery(label, fn, disabled) {
    const b = el('button', label); b.type = 'button'; b.disabled = disabled || active !== null; b.onclick = fn; $('recovery').append(b);
  }
  function render() {
    const focused = document.activeElement === $('answer'), selection = [$('answer').selectionStart, $('answer').selectionEnd];
    const row = current(), id = row?.request_id, entry = state.entries[id], draft = state.drafts[id];
    if (confirming && (confirming.id !== id || closeGate(row) || confirming.revision !== row.revision)) {
      // The confirmation named one exact question; never carry it to another.
      if (confirming.id === id) notices.set(id, 'This question changed, so nothing was closed. Review it before closing.');
      confirming = null;
    }
    $('composer').setAttribute('aria-busy', String(active !== null));
    $('refresh').disabled = loading;
    const verified = model?.source_current === true && !readError && Number.isSafeInteger(model.needs_answer_count) && model.needs_answer_count >= 0;
    $('intro').textContent = verified ? (model.needs_answer_count ? `Hi, you have ${model.needs_answer_count} open questions. Here is the ${state.seen.length > 1 ? 'next' : 'first'} one.` : 'Hi, you have 0 open questions needing an answer.') : loaded ? 'Current question count unavailable. Previously observed questions remain below.' : 'Loading open questions…';
    // Auto-refresh keeps a healthy list current; retry is only for a failed read.
    $('refresh').hidden = !loaded || verified;
    const checked = new Date(model?.checked_at);
    $('coverage').textContent = readError || (!model ? 'Checking source coverage…' : verified ? (Number.isFinite(checked.getTime()) ? 'Updated ' + time.format(checked) + '. ' : '') + 'Updates automatically.' : (model.error || 'Source coverage incomplete or unavailable.') + ' This is not a current count.');
    $('coverage').classList.toggle('warning', loaded && !verified);
    // The question comes first: a saved card shows at once, without loading text around it.
    $('intro').hidden = !model && !!row; $('coverage').hidden = !model && !!row;
    $('issues').replaceChildren();
    (model?.coverage_issues || []).forEach(issue => {
      const section = el('article', '', 'question'); section.append(ticket({...issue,adapter:'watchtower-block'}),el('h2',issue.title),el('div',issue.body,'raw'),el('p',issue.reason || 'No stable source identity. Read-only.','meta warning')); $('issues').append(section);
    });
    $('transcript').replaceChildren();
    // Answers still in flight stay on screen. Of the finished ones, only the latest
    // leaves a one-line receipt, and a Close keeps its undo there.
    let receipt = '';
    for (const seenId of state.seen) {
      const snapshot = state.snapshots[seenId], seenEntry = state.entries[seenId];
      const closedHere = state.closed[seenId] || state.closes[seenId];
      if (seenId === id || !snapshot || (!seenEntry && !closedHere)) continue;
      if (closedHere || seenEntry.saved?.state === 'accepted') receipt = state.closed[seenId] || (seenEntry ? `${snapshot.origin_id}: ${status(seenEntry)}` : receipt);
      else $('transcript').append(question(snapshot, seenEntry, true));
    }
    if (receipt) $('transcript').prepend(el('p', receipt, 'receipt accepted'));
    $('current').replaceChildren(); $('recovery').replaceChildren();
    if (row) {
      const snapshot = entry ? state.snapshots[id] || row : row;
      $('current').append(question(snapshot, entry));
      if (entry && (snapshot.body !== row.body || (row.status === 'open' && snapshot.revision !== row.revision))) {
        const changed = el('details', ''); changed.append(el('summary', 'Current source question has changed'), el('div', row.body, 'raw')); $('current').append(changed);
      }
      if (entry?.saved?.state !== 'accepted' && (!rows.some(r => r.request_id === id) || row.status !== 'open')) $('current').append(el('p', 'No longer open at source. Withdrawal is not delivery confirmation.', 'warning'));
    } else if (loaded) $('current').append(el('p', verified ? 'No next unanswered question.' : 'No verified questions to show. Use Try again.', 'meta'));
    const reason = entry ? frozenGate(!entry.saved ? 'record' : entry.saved.state === 'pending' ? 'send' : 'reconcile', row) : gate(row);
    const changed = draft && (draft.revision !== row?.revision || draft.body !== row?.body);
    $('answer').value = entry ? entry.payload.text : draft?.text || '';
    $('answerFields').hidden = !row || !!entry || !!reason;
    $('send').hidden = !row || !!entry || !!reason;
    $('answer').disabled = !row || !!entry || !!reason || active !== null;
    $('send').disabled = !row || !!entry || !!reason || !!changed || !draft?.text.trim() || active !== null;
    $('skip').disabled = active !== null || !row || openRows().length === 0;
    $('guidance').textContent = active === id ? (entry ? status(entry) : 'Closing…') : reason || (entry ? status(entry) : changed ? 'The question changed while you were drafting. Review the current question before rebinding your retained draft.' : 'Send answer records this exact text, then sends that frozen answer. No automatic retries.');
    if (draft && changed && !entry) {
      const previous = el('details',''); previous.append(el('summary','Question this draft was written for'),el('div',draft.body,'raw')); $('current').append(previous);
      recovery('Use draft for current question', () => {
        const r = current(); if (!r || gate(r) || active) return;
        state.drafts[id] = {...draft, revision:r.revision, body:r.body}; state.snapshots[id] = r; persist(); render();
      }, !!reason);
    }
    if (entry && !entry.saved) recovery('Retry saving same answer', () => act('record', id), !!reason);
    if (entry?.saved?.state === 'pending') recovery('Send saved answer', () => act('send', id), !!reason);
    if (entry?.saved && ['unknown','sending'].includes(entry.saved.state)) recovery('Check delivery', () => act('reconcile', id), !storageReady || model?.answers_enabled !== true);
    if (state.closes[id]) recovery('Check close', () => closeAct(id));
    const closeBlocked = !!closeGate(row);
    $('close').hidden = closeBlocked || !!confirming;
    $('close').disabled = active !== null;
    $('close').textContent = closesTicket(row) ? 'Close…' : 'Hide…';
    $('closeConfirm').hidden = !confirming;
    if (confirming) {
      $('closeText').textContent = closesTicket(row) ? `Close ${row.origin_id} as no longer relevant? The WatchTower ticket closes and no more work happens.` : 'Hide this question here? It is hidden in CCC only; its session keeps the question and nothing is sent.';
      $('closeYes').textContent = closesTicket(row) ? 'Yes, close ticket' : 'Yes, hide it';
      $('closeYes').disabled = $('closeKeep').disabled = active !== null;
    }
    if (notices.has(id)) $('current').append(el('p', notices.get(id), 'warning'));
    if (focused && !$('answer').disabled) { $('answer').focus({preventScroll:true}); $('answer').setSelectionRange(...selection); }
  }
  function validateReceipt(data, entry, id, action) {
    if (data.ok !== true || data.request_id !== id || !uuid(data.answer_id) || !states.includes(data.state)
        || !data.answer || data.answer.request_id !== id || data.answer.text !== entry.payload.text
        || data.answer.expected_revision !== entry.payload.expected_revision
        || (entry.saved && data.answer_id !== entry.saved.answer_id)
        || (action !== 'record' && !entry.saved)) throw Error('Unrecognized answer receipt; identity was not changed');
    return {answer_id:data.answer_id, state:data.state, text:data.answer.text, expected_revision:data.answer.expected_revision};
  }
  async function post(action, id, entry) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch('/api/decision-inbox/answers' + (action === 'record' ? '' : '/' + action), {
        method:'POST', headers:{'Content-Type':'application/json'}, signal:controller.signal,
        body:JSON.stringify(action === 'record' ? entry.payload : {answer_id:entry.saved.answer_id})
      });
      const data = await response.json();
      if (!response.ok || data.ok !== true) throw Error(data.error || 'Response unavailable');
      entry.saved = {...validateReceipt(data, entry, id, action), receipt:data.receipt};
      entry.deliveryUnknown = ['unknown','sending'].includes(entry.saved.state);
      if (!persist()) throw Error('Receipt received but recovery storage is unavailable; do not retry sending');
    } catch (error) {
      if (controller.signal.aborted) throw Error('The response took too long. Your exact answer is retained.');
      throw error;
    } finally { clearTimeout(timer); }
  }
  async function act(action, id, combined = false) {
    if (active || model?.answers_enabled !== true) return;
    const row = rows.find(r => r.request_id === id) || state.snapshots[id];
    let entry = state.entries[id];
    if (entry ? frozenGate(action, row) : gate(row)) return;
    if (action === 'record' && !entry) {
      const draft = state.drafts[id];
      if (!draft?.text.trim() || draft.revision !== row.revision || draft.body !== row.body) return;
      entry = {payload:{request_id:id, expected_revision:draft.revision, text:draft.text, idempotency_key:newKey()}};
      state.entries[id] = entry; state.snapshots[id] = {...row};
    }
    if (!entry || (action === 'record' && entry.saved) || (action === 'send' && entry.saved?.state !== 'pending')
        || (action === 'reconcile' && !['unknown','sending'].includes(entry.saved?.state))) return;
    // Unknown is durable BEFORE sending; interrupted delivery can only check receipts.
    if (action === 'send') { entry.saved = {...entry.saved, state:'unknown'}; entry.deliveryUnknown = true; }
    if (!persist()) { notices.set(id, 'Recovery storage unavailable. Nothing was sent by this action.'); render(); return; }
    active = id; notices.delete(id); render();
    let phase = action, advanced = false;
    try {
      await post(action, id, entry);
      // Only the original click authorizes this continuation, never reload or save retry.
      if (combined && entry.saved.state === 'pending' && !frozenGate('send', rows.find(r => r.request_id === id) || state.snapshots[id])) {
        phase = 'send'; entry.saved = {...entry.saved, state:'unknown'}; entry.deliveryUnknown = true;
        if (!persist()) throw Error('Cannot retain delivery recovery state. Nothing was sent');
        render(); await post('send', id, entry);
      }
      if (entry.saved.state === 'accepted' && state.selected === id) { next(false); advanced = true; }
    } catch (error) {
      notices.set(id, (phase === 'record' ? 'Save not confirmed. Retry saving the same answer; do not create a new payload. ' : 'Delivery not confirmed. Use Check delivery; do not resend. ') + error.message);
    } finally { active = null; render(); if (advanced) revealCurrent(); }
    if (entry.saved?.state === 'accepted') refresh();  // The count must drop now, not at the next poll.
  }
  async function closeAct(id) {
    if (active) return;
    let close = state.closes[id];
    if (!close) {
      const row = current();
      if (!confirming || confirming.id !== id || closeGate(row)) return;
      close = {request_id:id, expected_revision:confirming.revision, idempotency_key:newKey()};
      state.closes[id] = close; state.snapshots[id] = {...row};
    }
    // The key is durable BEFORE the request, so a lost response replays it.
    if (!persist()) { delete state.closes[id]; confirming = null; notices.set(id, 'Recovery storage unavailable. Nothing was closed.'); render(); return; }
    active = id; confirming = null; notices.delete(id); render();
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 70000);
    let advanced = false;
    try {
      const response = await fetch('/api/decision-inbox/close', {method:'POST', headers:{'Content-Type':'application/json'}, signal:controller.signal, body:JSON.stringify(close)});
      const data = await response.json();
      if (definiteRefusals.includes(data.error)) {
        delete state.closes[id]; persist();
        notices.set(id, data.error === 'answer_in_progress' ? 'Nothing was closed: an answer to this question is saved or on its way.' : data.error === 'conflict' ? 'Nothing was closed: this question changed or already ended at the source.' : 'Nothing was closed: this source cannot close the question here.');
      } else if (data.ok === true && data.state === 'closed' && data.request_id === id) {
        const ref = data.origin_id || id;
        state.closed[id] = data.scope === 'source' ? `Closed ${ref}. No more work will happen. To undo, run: wt reopen ${ref}` : 'Hidden here only. The originating session still has this question.';
        delete state.closes[id]; delete state.drafts[id];
        if (!state.seen.includes(id)) state.seen.push(id);
        persist();
        if (state.selected === id) { next(false); advanced = true; }
      } else throw Error('The source did not confirm it');
    } catch (error) {
      notices.set(id, 'Close not confirmed. Use Check close; it repeats this same close and cannot close anything else.');
    } finally { clearTimeout(timer); active = null; render(); if (advanced) { revealCurrent(); refresh(); } }
  }
  async function refresh() {
    if (loading) return;
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 30000);
    loading = true; render();
    try {
      const response = await fetch('/api/decision-inbox/requests', {cache:'no-store', signal:controller.signal});
      const data = await response.json();
      if (!response.ok || data.ok !== true || !Array.isArray(data.requests)) throw Error(data.error || 'Cannot read source questions.');
      rows = data.requests; model = data; readError = '';
      rows.forEach(absorb); persist();
      const selected = rows.find(r => r.request_id === state.selected);
      const entry = state.entries[state.selected];
      // Confirmed answers belong in history. Keep drafts and uncertain deliveries
      // selected for recovery; a disappearing question is not an answer receipt.
      if (entry?.saved?.state === 'accepted'
          || (!entry && !state.closes[state.selected] && !state.drafts[state.selected] && (!selected || selected.status !== 'open'))) {
        next(entry?.saved?.state === 'accepted');
      } else if (!state.selected) {
        const first = openRows().find(r => state.entries[r.request_id]?.saved?.state !== 'accepted');
        if (first) select(first.request_id);
      }
    } catch (error) { readError = error.message + ' Coverage unavailable; this is not a current count.'; }
    finally {
      clearTimeout(timer);
      loading = false; loaded = true; render();
      const count = !readError && model?.source_current === true && Number.isSafeInteger(model.needs_answer_count) && model.needs_answer_count >= 0 ? model.needs_answer_count : null;
      if (parent !== window) parent.postMessage({type:'ccc:decision-count', count}, location.origin);
    }
  }
  $('answer').oninput = () => {
    const row = current(); if (!row || state.entries[row.request_id] || gate(row)) return;
    const draft = state.drafts[row.request_id] || {revision:row.revision, body:row.body};
    state.drafts[row.request_id] = {...draft, text:$('answer').value}; state.snapshots[row.request_id] = row;
    // Do not rebuild the live transcript on each keystroke or disturb the caret.
    if (!persist()) { render(); return; }
    $('send').disabled = !state.drafts[row.request_id].text.trim() || draft.revision !== row.revision || draft.body !== row.body || active !== null;
  };
  $('composer').onsubmit = event => { event.preventDefault(); if (!$('send').disabled) act('record', state.selected, true); };
  $('skip').onclick = () => next(); $('refresh').onclick = refresh;
  $('close').onclick = () => {
    const row = current(); if (active || closeGate(row)) return;
    confirming = {id:row.request_id, revision:row.revision}; notices.delete(row.request_id); render(); $('closeKeep').focus({preventScroll:true});
  };
  $('closeKeep').onclick = () => { confirming = null; render(); };
  $('closeYes').onclick = () => { if (confirming) closeAct(confirming.id); };
  function refreshVisible() {
    if (document.hidden || active !== null) return;
    // A retained sidebar iframe is still a visible document while its frame is
    // hidden. Avoid source subprocesses for inactive sidebar/mobile views.
    if (window.frameElement && !window.frameElement.getClientRects().length) return;
    refresh();
  }
  window.addEventListener('pageshow', event => { if (event.persisted) refreshVisible(); });
  document.addEventListener('visibilitychange', refreshVisible);
  window.addEventListener('message', event => {
    if (parent !== window && event.source === parent && event.origin === location.origin
        && event.data?.type === 'ccc:decision-refresh') refreshVisible();
  });
  setInterval(refreshVisible, 30000);
  refresh();
})();
