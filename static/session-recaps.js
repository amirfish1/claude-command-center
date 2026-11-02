/* Old owner waits get context when they first become visible. The server owns
 * admission, revision identity and all spending limits, across tabs/restarts. */
(() => {
  'use strict';
  const cache = new Map();
  const pending = new Set();
  let scheduled = false;
  const esc = value => String(value || '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
  const notes = {
    disabled: 'Saved recap · automatic rechecking is off.',
    checking: 'Checking whether this still needs you…',
    queued: 'Saved recap · another recap is being checked.',
    daily_limit: 'Saved recap · today’s recheck limit reached.',
    cooldown: 'Saved recap · waiting before checking changed evidence.',
    unverified: 'Saved recap · recheck unavailable; no automatic retry.',
    checked: 'Rechecked from saved conversation and linked-ticket evidence. Past test and delivery claims remain self-reported.'
  };
  function eligible(row, info, now = Date.now()) {
    const activity = Math.max(Number(row.modified) || 0, Number(row.mtime) || 0);
    return activity > 0 && now / 1000 - activity >= 86400
      && !row.archived && !row.trashed && !row.explicitly_archived
      && row.last_event_type !== 'user' && info.kind === 'next';
  }
  function contents(value) {
    const recap = value.recap || {};
    const result = value.status === 'checked' ? value.result : null;
    const date = recap.last_activity ? new Date(recap.last_activity * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '';
    const line = (label, text) => text ? '<div><strong>' + label + '</strong> ' + esc(text) + '</div>' : '';
    return '<summary>Recap · ' + esc(result?.goal || recap.goal || 'Earlier work') + '</summary>'
      + '<div class="conv-old-recap-body">'
      + line('Last worked:', date)
      + line('About:', result?.goal || recap.goal)
      + line('Reported done:', result?.shipped || recap.did)
      + line('Recorded request:', recap.request)
      + (result ? line('Recheck:', result.needs_you) + line('Finding:', result.finding) : '')
      + '<p class="conv-old-recap-note">' + esc(notes[value.status] || 'Saved recap · waiting to check.') + '</p></div>';
  }
  function render(row, info) {
    if (!eligible(row, info)) return '';
    const sid = String(row.session_id || row.id || '');
    const state = row.session_state || {};
    const fingerprint = JSON.stringify([row.modified, row.mtime, state, info.text]);
    let value = cache.get(sid);
    if (!value || value.fingerprint !== fingerprint) {
      value = { fingerprint, status: 'saved', recap: {
        goal: state.completion?.scope?.goal || row.display_name || row.title || 'Earlier work',
        did: state.did || '', request: info.text, last_activity: Math.max(Number(row.modified) || 0, Number(row.mtime) || 0)
      } };
      cache.set(sid, value);
    }
    if (value.status === 'ineligible') return '';
    if (value.retry_at && Date.now() / 1000 >= value.retry_at) value.requested = false;
    return '<details class="conv-old-recap" data-recap-session="' + esc(sid) + '" onclick="event.stopPropagation()"'
      + ' onpointerdown="event.stopPropagation()">' + contents(value) + '</details>';
  }
  function paint(sid, value) {
    for (const el of document.querySelectorAll('.conv-old-recap')) {
      if (el.dataset.recapSession !== sid) continue;
      const html = contents(value);
      if (el.dataset.recapHtml !== html) {
        el.innerHTML = html;
        el.dataset.recapHtml = html;
      }
    }
  }
  async function flush() {
    scheduled = false;
    const ids = [...pending].slice(0, 20);
    ids.forEach(sid => pending.delete(sid));
    if (!ids.length) return;
    const generations = new Map(ids.map(sid => [sid, cache.get(sid)?.fingerprint]));
    try {
      const response = await fetch('/api/sessions/old-recaps', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_ids: ids }) });
      if (!response.ok) throw new Error('Recaps unavailable');
      const data = await response.json();
      if (!data.ok) throw new Error('Recaps unavailable');
      for (const sid of ids) {
        const value = cache.get(sid);
        if (!value || value.fingerprint !== generations.get(sid)) continue;
        const result = data.sessions?.[sid];
        if (!result) continue;
        Object.assign(value, result, { requested: true });
        // A changed or answered request must not retain an old agent result.
        if (result.status === 'ineligible') {
          for (const el of document.querySelectorAll('.conv-old-recap')) {
            if (el.dataset.recapSession === sid) el.remove();
          }
          continue;
        }
        paint(sid, value);
        if (['checking', 'queued'].includes(value.status)) {
          setTimeout(() => {
            if (cache.get(sid) === value && document.querySelector('.conv-old-recap[data-recap-session="' + CSS.escape(sid) + '"]')) {
              value.requested = false;
              scan();
            }
          }, 10000);
        }
      }
    } catch (_) {
      for (const sid of ids) {
        const value = cache.get(sid);
        if (value && value.fingerprint === generations.get(sid)) {
          Object.assign(value, { status: 'unverified', requested: true });
          paint(sid, value);
        }
      }
    }
    if (pending.size && !scheduled) { scheduled = true; setTimeout(flush, 100); }
  }
  const observer = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting || document.hidden) continue;
      const sid = entry.target.dataset.recapSession;
      const value = cache.get(sid);
      if (!value || value.requested) continue;
      value.requested = true;
      pending.add(sid);
    }
    if (pending.size && !scheduled) { scheduled = true; setTimeout(flush, 100); }
  });
  function scan() {
    for (const el of document.querySelectorAll('.conv-old-recap')) {
      observer?.unobserve(el);
      observer?.observe(el);
    }
  }
  window.CCCSessionRecaps = { render, eligible, contents };
  document.addEventListener('DOMContentLoaded', () => {
    const list = document.getElementById('convList');
    if (list) new MutationObserver(scan).observe(list, { childList: true, subtree: true });
    scan();
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) scan(); });
})();
