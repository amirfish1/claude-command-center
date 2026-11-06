/* Share this run: local redacted replay, weekly stats card, README badge.
 * Every request here goes to this dashboard's own /api. Saving writes a file
 * through the browser; nothing is uploaded and no third party is contacted. */
(() => {
  'use strict';
  const REPO_URL = 'https://github.com/amirfish1/claude-command-center';
  const BADGE_URL = 'https://raw.githubusercontent.com/amirfish1/claude-command-center/main/static/built-with-ccc.svg';
  const BADGE_MD = '[![Built with CCC](' + BADGE_URL + ')](' + REPO_URL + ')';
  const FIRST_SEEN_KEY = 'ccc.shareRun.firstSeen';
  const NUDGE_DONE_KEY = 'ccc.shareRun.nudgeDone';
  const DAY_MS = 86400000;
  const NUDGE_DELAY_MS = 20000;
  const MIN_NUDGE_AGENTS = 3;
  const SAFETY_NOTE = 'Built on this machine. Nothing is uploaded: you get a file and decide where it goes. '
    + 'Redaction hides names it does not recognize, but a private name that is also a plain word can survive, so read the preview first.';

  let overlay = null;
  let current = { sid: '', tab: 'run', exported: null, stats: null, days: 7 };

  const el = (tag, attrs, ...kids) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (key === 'on') for (const [name, fn] of Object.entries(value)) node.addEventListener(name, fn);
      else if (key === 'text') node.textContent = value;
      else if (value !== false && value != null) node.setAttribute(key, value === true ? '' : value);
    }
    kids.flat().forEach(kid => kid && node.append(kid));
    return node;
  };

  function saveBlob(blob, name) {
    const url = URL.createObjectURL(blob);
    const link = el('a', { href: url, download: name });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  const svgDataUrl = svg => 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);

  function savePng(svg, name) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => {
        const canvas = document.createElement('canvas');
        canvas.width = 2400;
        canvas.height = 1260;
        canvas.getContext('2d').drawImage(image, 0, 0, 2400, 1260);
        canvas.toBlob(blob => {
          if (!blob) return reject(new Error('image failed'));
          saveBlob(blob, name);
          resolve();
        }, 'image/png');
      };
      image.onerror = () => reject(new Error('image failed'));
      image.src = svgDataUrl(svg);
    });
  }

  async function fetchRun(sid, form) {
    const query = new URLSearchParams();
    if (form.hide) query.set('hide', form.hide);
    if (form.keep) query.set('keep', form.keep);
    if (form.title) query.set('title', form.title);
    const res = await fetch('/api/conversations/' + encodeURIComponent(sid) + '/share?' + query, { cache: 'no-store' });
    return res.json();
  }

  async function fetchStats(days) {
    const res = await fetch('/api/share/stats?days=' + days, { cache: 'no-store' });
    return res.json();
  }

  function ensureOverlay() {
    if (overlay) return overlay;
    overlay = el('div', { class: 'sr-overlay', hidden: true, role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Share' });
    overlay.addEventListener('click', ev => { if (ev.target === overlay) close(); });
    document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && overlay && !overlay.hidden) close(); });
    document.body.append(overlay);
    return overlay;
  }

  function close() { if (overlay) { overlay.hidden = true; overlay.replaceChildren(); } }

  function render() {
    const tabs = [['run', 'This run'], ['week', 'My week'], ['badge', 'README badge']]
      .filter(([id]) => id !== 'run' || current.sid);
    const head = el('div', { class: 'sr-head' },
      el('h2', { text: 'Share' }),
      tabs.map(([id, label]) => el('button', {
        type: 'button', class: 'sr-tab', role: 'tab', 'aria-selected': String(current.tab === id), text: label,
        on: { click: () => { current.tab = id; render(); } },
      })),
      el('button', { type: 'button', class: 'sr-close', 'aria-label': 'Close', text: 'Close', on: { click: close } }));
    const body = el('div', { class: 'sr-body' });
    ensureOverlay().replaceChildren(el('div', { class: 'sr-dialog' }, head, body));
    overlay.hidden = false;
    ({ run: renderRun, week: renderWeek, badge: renderBadge })[current.tab](body);
  }

  function renderRun(body) {
    const preview = el('div', { class: 'sr-preview' }, el('div', { class: 'sr-empty', text: 'Redacting…' }));
    const summary = el('p', { class: 'sr-note' });
    const hide = el('input', { type: 'text', placeholder: 'client, project, a colleague' });
    const keep = el('input', { type: 'text', placeholder: 'words the redactor over-hid' });
    const title = el('input', { type: 'text', placeholder: 'From the first prompt' });
    const savePage = el('button', { type: 'button', class: 'sr-btn sr-btn-primary', disabled: true, text: 'Save page' });
    const saveImage = el('button', { type: 'button', class: 'sr-btn', disabled: true, text: 'Save image' });
    const apply = el('button', { type: 'button', class: 'sr-btn', text: 'Apply' });
    savePage.addEventListener('click', () => {
      saveBlob(new Blob([current.exported.html], { type: 'text/html' }), 'agent-run-replay.html');
    });
    saveImage.addEventListener('click', () => {
      savePng(current.exported.svg, 'agent-run.png').catch(() => { summary.textContent = 'The image could not be built.'; });
    });
    async function load() {
      current.exported = null;
      savePage.disabled = saveImage.disabled = true;
      preview.replaceChildren(el('div', { class: 'sr-empty', text: 'Redacting…' }));
      summary.textContent = '';
      summary.classList.remove('sr-error');
      let data;
      try {
        data = await fetchRun(current.sid, { hide: hide.value, keep: keep.value, title: title.value });
      } catch (_) {
        data = { ok: false, error: 'Could not reach the dashboard.' };
      }
      if (!data.ok) {
        summary.textContent = data.error || 'Nothing could be exported.';
        summary.classList.add('sr-error');
        preview.replaceChildren(el('div', { class: 'sr-empty', text: 'Nothing was exported.' }));
        return;
      }
      current.exported = data;
      const frame = el('iframe', { sandbox: 'allow-scripts', title: 'Redacted replay preview' });
      frame.srcdoc = data.html;
      preview.replaceChildren(frame);
      summary.textContent = data.hidden_summary;
      savePage.disabled = saveImage.disabled = false;
    }
    apply.addEventListener('click', load);
    body.append(preview, el('div', { class: 'sr-side' },
      el('p', { class: 'sr-note', text: 'Redaction is always on. Tool output, tool arguments and timestamps are left out.' }),
      summary,
      el('label', { text: 'Also hide (comma separated)' }, hide),
      el('label', { text: 'Keep these words' }, keep),
      el('label', { text: 'Title' }, title),
      el('div', { class: 'sr-row' }, apply),
      el('div', { class: 'sr-row' }, savePage, saveImage),
      el('p', { class: 'sr-note', text: SAFETY_NOTE })));
    load();
  }

  function renderWeek(body) {
    const preview = el('div', { class: 'sr-preview' }, el('div', { class: 'sr-empty', text: 'Counting…' }));
    const saveImage = el('button', { type: 'button', class: 'sr-btn sr-btn-primary', disabled: true, text: 'Save image' });
    const note = el('p', { class: 'sr-note' });
    let tries = 0;
    async function load() {
      saveImage.disabled = true;
      let data;
      try { data = await fetchStats(current.days); } catch (_) { data = { ok: false }; }
      if (overlay.hidden || current.tab !== 'week') return;
      if (data.ok && data.pending && tries < 10) {
        tries += 1;
        setTimeout(load, 3000);
        return;
      }
      if (!data.ok || data.pending) {
        preview.replaceChildren(el('div', { class: 'sr-empty', text: 'The numbers are not ready yet. Try again in a minute.' }));
        return;
      }
      current.stats = data;
      preview.replaceChildren(el('img', { src: svgDataUrl(data.svg), alt: data.agents + ' agents in the last ' + data.days + ' days' }));
      note.textContent = data.commits == null
        ? 'Commit counts are still being collected, so the card leaves them out.'
        : 'Agents are sessions with activity in this period. Commits are your commits that reached a remote.';
      saveImage.disabled = false;
    }
    saveImage.addEventListener('click', () => {
      savePng(current.stats.svg, 'my-agent-week.png').catch(() => { note.textContent = 'The image could not be built.'; });
    });
    const period = days => el('button', {
      type: 'button', class: 'sr-tab', 'aria-selected': String(current.days === days),
      text: days === 7 ? 'This week' : 'Last 30 days',
      on: { click: () => { current.days = days; render(); } },
    });
    body.append(preview, el('div', { class: 'sr-side' },
      el('div', { class: 'sr-row' }, period(7), period(30)),
      el('p', { class: 'sr-note', text: 'Totals only. No session names, repos or paths are on this card.' }),
      note,
      el('div', { class: 'sr-row' }, saveImage),
      el('p', { class: 'sr-note', text: 'Saved to this machine. Nothing is uploaded.' })));
    load();
  }

  function renderBadge(body) {
    const snippet = el('textarea', { readonly: true, 'aria-label': 'Badge Markdown' });
    snippet.value = BADGE_MD;
    const copy = el('button', { type: 'button', class: 'sr-btn sr-btn-primary', text: 'Copy Markdown' });
    copy.addEventListener('click', async () => {
      snippet.select();
      try { await navigator.clipboard.writeText(BADGE_MD); copy.textContent = 'Copied'; }
      catch (_) { copy.textContent = 'Press Ctrl+C'; }
    });
    body.append(
      el('div', { class: 'sr-preview' }, el('div', { class: 'sr-empty' },
        el('img', { src: '/static/built-with-ccc.svg', alt: 'Built with CCC', style: 'width:244px' }))),
      el('div', { class: 'sr-side' },
        el('p', { class: 'sr-note', text: 'A fixed image for your README. It carries no stats and no tracking.' }),
        el('label', { text: 'Markdown' }, snippet),
        el('div', { class: 'sr-row' }, copy)));
  }

  function open(options) {
    const sid = (options && options.sid) || '';
    current = { sid, tab: (options && options.tab) || (sid ? 'run' : 'week'), exported: null, stats: null, days: 7 };
    render();
  }

  // Shown at most once, between day 1 and day 7, and only when there is
  // something real to show. It asks for no post, star, follow or sign-up.
  function shouldNudge(state) {
    if (state.done || !state.firstSeen) return false;
    const age = state.now - state.firstSeen;
    return age >= DAY_MS && age <= 7 * DAY_MS;
  }

  function showNudge(stats) {
    const parts = [stats.agents + ' agents'];
    if (stats.commits) parts.push(stats.commits + ' commits');
    const card = el('div', { class: 'sr-nudge', role: 'status' },
      el('p', { text: 'Your first week with CCC: ' + parts.join(', ') + '. Want a card of that? It stays on this machine unless you share it yourself.' }),
      el('div', { class: 'sr-row' },
        el('button', { type: 'button', class: 'sr-btn sr-btn-primary', text: 'Make my card', on: { click: () => { card.remove(); open({ tab: 'week' }); } } }),
        el('button', { type: 'button', class: 'sr-btn', text: 'No thanks', on: { click: () => card.remove() } })));
    document.body.append(card);
  }

  async function maybeNudge(now) {
    let store;
    try { store = window.localStorage; store.getItem(NUDGE_DONE_KEY); } catch (_) { return; }
    let firstSeen = Number(store.getItem(FIRST_SEEN_KEY)) || 0;
    if (!firstSeen) {
      firstSeen = now;
      store.setItem(FIRST_SEEN_KEY, String(now));
    }
    if (!shouldNudge({ now, firstSeen, done: !!store.getItem(NUDGE_DONE_KEY) })) return;
    if (document.hidden || document.querySelector('.sr-overlay:not([hidden])')) return;
    let stats;
    try { stats = await fetchStats(7); } catch (_) { return; }
    if (!stats || !stats.ok || stats.pending || (stats.agents || 0) < MIN_NUDGE_AGENTS) return;
    store.setItem(NUDGE_DONE_KEY, '1');
    showNudge(stats);
  }

  document.addEventListener('click', ev => {
    const item = ev.target && ev.target.closest && ev.target.closest('[data-simple-action="share-run"]');
    if (!item) return;
    open({ sid: typeof window.cccFrontSessionId === 'function' ? window.cccFrontSessionId() : '' });
  });
  setTimeout(() => maybeNudge(Date.now()), NUDGE_DELAY_MS);

  window.cccShareRun = { open, maybeNudge, shouldNudge };
})();
