/* Agent config access: CCC asks before writing into the user's agent config.
 *
 * Backend: /api/config-consent (ccc_server/config_consent.py). Every file CCC
 * would change outside ~/.claude/command-center (Claude/Codex hooks, skills
 * folders) is an item with the exact diff. Nothing is written until the user
 * approves it here or with `ccc consent`.
 *
 * - First run (or a new/changed item for an installed agent): a small
 *   OK / Not now message opens on its own; "Details" expands the full list.
 * - Installs from before this gate: a one-time "already in your config" list
 *   with Keep / Remove.
 * - Settings > Maintenance > Agent config access reopens it any time, with
 *   "Remove everything CCC installed".
 * - #configConsentPill says when something needs review, or that live
 *   status is off because the Claude Code hooks were declined.
 */
(function () {
  'use strict';
  if (window.cccConfigConsent) return;

  const REFRESH_MS = 5 * 60 * 1000;
  const SNOOZE_KEY = 'ccc-config-consent-snooze';
  const SNOOZE_MS = 24 * 60 * 60 * 1000;
  const STATUS_BADGE = {
    pending: ['New', 'cfgc-badge-new'],
    changed: ['Changed', 'cfgc-badge-changed'],
    enabled: ['Approved', 'cfgc-badge-on'],
    declined: ['Off', 'cfgc-badge-off'],
  };
  // [value, label] per status. '' = leave as is.
  const CHOICES = {
    pending: [['approve', 'Approve'], ['decline', 'Skip']],
    changed: [['approve', 'Approve update'], ['decline', 'Remove']],
    enabled: [['', 'Keep'], ['decline', 'Remove']],
    declined: [['', 'Leave off'], ['approve', 'Approve']],
  };

  let state = null;
  let $modal = null;
  let mode = 'auto';
  let focusId = '';
  let expanded = false;
  const ENGINE_LABEL = { claude: 'Claude Code', codex: 'Codex' };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function isPopout() {
    try { return new URLSearchParams(location.search).has('ccc_popout'); } catch (_) { return false; }
  }

  // needs_review for an agent that is installed here (older servers: all).
  function due(i) { return i.auto_review === undefined ? i.needs_review : i.auto_review; }

  function signature(s) {
    return (s.items || []).filter(due)
      .map((i) => i.id + ':' + i.status).sort().join('|') + (s.notice && s.notice.pending ? '|notice' : '');
  }

  function snoozed(s) {
    try {
      const v = JSON.parse(localStorage.getItem(SNOOZE_KEY) || 'null');
      return !!(v && v.sig === signature(s) && v.until > Date.now());
    } catch (_) { return false; }
  }

  function snooze() {
    if (!state) return;
    try { localStorage.setItem(SNOOZE_KEY, JSON.stringify({ sig: signature(state), until: Date.now() + SNOOZE_MS })); } catch (_) {}
  }

  function load() {
    return fetch('/api/config-consent', { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => {
        if (s && s.ok) {
          state = s;
          renderPill();
        }
        return state;
      })
      .catch(() => state);
  }

  function hooksItem() {
    return ((state && state.items) || []).find((i) => i.id === 'claude-hooks');
  }

  function renderPill() {
    const $pill = document.getElementById('configConsentPill');
    const $text = document.getElementById('configConsentPillText');
    if (!$pill || !state) return;
    const n = state.auto_review === undefined ? (state.needs_review || 0) : state.auto_review;
    const hooks = hooksItem();
    if (n > 0) {
      $pill.hidden = false;
      $pill.classList.add('visible');
      if ($text) $text.textContent = n === 1 ? '1 config change to review' : n + ' config changes to review';
      $pill.title = 'CCC is waiting for your OK before it adds hooks or skills to your agent config. Click to review the exact changes.';
      $pill.dataset.focus = '';
    } else if (hooks && hooks.status === 'declined') {
      $pill.hidden = false;
      $pill.classList.add('visible');
      if ($text) $text.textContent = 'Live status off';
      $pill.title = "CCC's Claude Code hooks are off, so these don't work for Claude sessions: "
        + hooks.depends + ' Click to review or enable them.';
      $pill.dataset.focus = 'claude-hooks';
    } else {
      $pill.hidden = true;
      $pill.classList.remove('visible');
    }
  }

  // The first-run engines screen, another dialog, or the First Flight tour (which itself waits
  // for .upd-overlay.open, i.e. this dialog) is on screen.
  function otherOverlayOpen() {
    return !!window.__cccTourActive || !!window.__cccEnginesFirstRun || Array.from(document.querySelectorAll('.upd-overlay.open'))
      .some((el) => el.id !== 'cfgConsentModal');
  }

  function diffHtml(text) {
    return String(text || '').split('\n').map((line) => {
      let cls = '';
      if (line.startsWith('+++') || line.startsWith('---')) cls = 'cfgc-d-file';
      else if (line.startsWith('@@')) cls = 'cfgc-d-hunk';
      else if (line.startsWith('+')) cls = 'cfgc-d-add';
      else if (line.startsWith('-')) cls = 'cfgc-d-del';
      return '<span class="' + cls + '">' + esc(line) + '</span>';
    }).join('\n');
  }

  function itemHtml(item) {
    const badge = item.skipped_by_env ? ['Skipped (env)', 'cfgc-badge-off'] : (STATUS_BADGE[item.status] || [item.status, '']);
    const choices = item.skipped_by_env ? [] : (CHOICES[item.status] || []);
    const name = 'cfgc-' + item.id.replace(/[^a-z0-9-]/gi, '_');
    // Default: leave as is. Opened from the "Live status off" pill, the
    // focused item starts on Approve so enabling it is one Save away.
    const values = choices.map((c) => c[0]);
    const initial = item.id === focusId && values.includes('approve') ? 'approve'
      : (values.includes('') ? '' : null);
    const radios = choices.map(([value, label]) => (
      '<label class="cfgc-choice"><input type="radio" name="' + esc(name) + '" value="' + esc(value) + '"'
      + (value === initial ? ' checked' : '') + '> ' + esc(label) + '</label>'
    )).join('');
    const showRemoval = (item.status === 'enabled' || item.status === 'declined') && item.installed;
    const blocks = showRemoval ? (item.removal || []) : (item.changes || []);
    const diffLabel = showRemoval ? 'What Remove would change' : 'Show the exact change';
    const diffText = blocks.map((b) => b.diff || '').join('\n').trim();
    const targets = (item.targets || []).length ? item.targets : blocks.map((b) => b.path);
    let extra = '';
    if (item.status === 'changed') {
      extra = '<div class="cfgc-note">CCC\'s version of this changed since you approved it. '
        + 'The older version stays in place until you approve the update.</div>';
    }
    if (item.status === 'declined' && item.installed) {
      extra = '<div class="cfgc-note">Still present in your config: CCC won\'t touch it again, but you can remove it.</div>';
    }
    return '<div class="cfgc-item' + (item.id === focusId ? ' cfgc-focus' : '') + '" data-id="' + esc(item.id) + '">'
      + '<div class="cfgc-head">'
      + '<span class="cfgc-name">' + esc(item.title) + '</span>'
      + '<span class="cfgc-badge ' + badge[1] + '">' + esc(badge[0]) + '</span>'
      + '<span class="cfgc-choices" role="radiogroup" aria-label="' + esc(item.title) + '">' + radios + '</span>'
      + '</div>'
      + '<div class="cfgc-summary">' + esc(item.summary) + '</div>'
      + '<div class="cfgc-depends"><strong>Needed for:</strong> ' + esc(item.depends) + '</div>'
      + (targets.length ? '<div class="cfgc-files">' + targets.map((t) => '<code>' + esc(t) + '</code>').join(' ') + '</div>' : '')
      + extra
      + (item.error ? '<div class="cfgc-err">' + esc(item.error) + '</div>' : '')
      + (diffText ? '<details class="cfgc-details"><summary>' + esc(diffLabel) + '</summary><pre class="cfgc-diff">' + diffHtml(diffText) + '</pre></details>'
        : (item.up_to_date && item.status !== 'declined' ? '<div class="cfgc-muted">Already up to date in your config.</div>' : ''))
      + '</div>';
  }

  function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }

  // "CCC will add 4 skills and 6 hooks to your Claude Code setup. ..." from
  // the items actually waiting, for the agents actually installed.
  function summaryHtml(review, carried) {
    const present = (state && state.engines_present) || { claude: true, codex: false };
    const names = [];
    review.forEach((i) => (i.engines || ['claude']).forEach((e) => {
      const l = ENGINE_LABEL[e];
      if (l && present[e] !== false && names.indexOf(l) < 0) names.push(l);
    }));
    const where = 'your ' + (names.join(' and ') || 'agent') + ' setup';
    const safe = 'It only adds its own files (backed up first, undo anytime) and changes nothing else.';
    if (!review.length) {
      const n = carried.length;
      return 'Earlier versions of CCC added ' + plural(n, 'item') + ' to your agent setup. '
        + 'They keep working, and you can remove any of them in Details.';
    }
    const skills = review.filter((i) => i.id.indexOf('skill:') === 0).length;
    const wtSkills = review.some((i) => i.id === 'watchtower-skills');
    const hooks = review.reduce((a, i) => a + (i.hook_count || 0), 0);
    const parts = [];
    if (skills) parts.push(plural(skills, 'skill'));
    if (hooks) parts.push(plural(hooks, 'hook'));
    if (wtSkills) parts.push('the WatchTower skills');
    const list = parts.length > 1 ? parts.slice(0, -1).join(', ') + ' and ' + parts[parts.length - 1] : parts[0];
    const allChanged = review.every((i) => i.status === 'changed');
    const lead = allChanged
      ? 'CCC has updated ' + list + ' in ' + where + '.'
      : 'CCC will add ' + list + ' to ' + where + '.';
    const why = hooks && (skills || wtSkills) ? 'They let the dashboard show live status and let your agents use CCC.'
      : hooks ? 'They let the dashboard show live status.' : 'They let your agents use CCC.';
    return esc(lead + ' ' + safe + ' ' + why);
  }

  function section(title, note, items) {
    if (!items.length) return '';
    return '<div class="cfgc-section"><div class="cfgc-sec-title">' + esc(title) + '</div>'
      + (note ? '<div class="cfgc-sec-note">' + esc(note) + '</div>' : '')
      + items.map(itemHtml).join('') + '</div>';
  }

  function ensureModal() {
    if ($modal) return $modal;
    $modal = document.createElement('div');
    $modal.id = 'cfgConsentModal';
    $modal.className = 'upd-overlay';
    $modal.setAttribute('role', 'dialog');
    $modal.setAttribute('aria-modal', 'true');
    $modal.setAttribute('aria-labelledby', 'cfgConsentTitle');
    $modal.innerHTML = '<div class="upd-backdrop" data-role="cfgc-backdrop"></div>'
      + '<div class="upd-dialog cfgc-dialog">'
      + '<div class="upd-title" id="cfgConsentTitle">Agent config access</div>'
      + '<div class="cfgc-brief" data-role="cfgc-brief"></div>'
      + '<div class="cfgc-intro" data-role="cfgc-intro"></div>'
      + '<div class="cfgc-body" data-role="cfgc-body"></div>'
      + '<div class="upd-error" data-role="cfgc-error"></div>'
      + '<div class="upd-actions cfgc-actions">'
      + '<button type="button" class="upd-btn cfgc-revoke-all" data-role="cfgc-revoke-all">Remove everything CCC installed</button>'
      + '<span class="cfgc-spacer"></span>'
      + '<button type="button" class="upd-btn cfgc-details-btn" data-role="cfgc-details">Details</button>'
      + '<button type="button" class="upd-btn" data-role="cfgc-later">Not now</button>'
      + '<button type="button" class="upd-btn upd-primary" data-role="cfgc-ok">OK</button>'
      + '<button type="button" class="upd-btn" data-role="cfgc-approve-all">Approve all</button>'
      + '<button type="button" class="upd-btn upd-primary" data-role="cfgc-save">Save choices</button>'
      + '</div></div>';
    document.body.appendChild($modal);
    $modal.querySelector('[data-role="cfgc-backdrop"]').addEventListener('click', () => close(true));
    $modal.querySelector('[data-role="cfgc-later"]').addEventListener('click', () => close(true));
    $modal.querySelector('[data-role="cfgc-save"]').addEventListener('click', () => save(false));
    $modal.querySelector('[data-role="cfgc-approve-all"]').addEventListener('click', () => save(true));
    $modal.querySelector('[data-role="cfgc-ok"]').addEventListener('click', () => save('auto'));
    $modal.querySelector('[data-role="cfgc-details"]').addEventListener('click', () => { expanded = true; render(); });
    $modal.querySelector('[data-role="cfgc-revoke-all"]').addEventListener('click', revokeAll);
    $modal.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); close(true); }
    });
    return $modal;
  }

  function render() {
    const m = ensureModal();
    const items = (state && state.items) || [];
    const noticeIds = new Set(((state && state.notice && state.notice.items) || []).map((i) => i.id));
    const noticeOn = !!(state && state.notice && state.notice.pending);
    const compact = mode === 'auto' && !expanded;
    // Automatic prompt: only agents that are installed. The Settings entry
    // (manage mode) lists everything.
    const review = items.filter((i) => (mode === 'auto' ? due(i) : i.needs_review));
    const carried = noticeOn ? items.filter((i) => noticeIds.has(i.id) && !i.needs_review) : [];
    const rest = mode === 'manage'
      ? items.filter((i) => !i.needs_review && !(noticeOn && noticeIds.has(i.id))) : [];
    const intro = 'CCC can add a few hooks and skills to your agent config so the dashboard can show live '
      + 'status and your agents can use CCC. It only changes the files listed below, only after you say so, '
      + 'keeps everything else in them as-is, and saves a backup of each file first ('
      + '<code>' + esc((state && state.backups_dir) || '') + '</code>). You can undo any of it here later.';
    m.querySelector('.cfgc-dialog').classList.toggle('cfgc-compact', compact);
    m.querySelector('[data-role="cfgc-brief"]').innerHTML = summaryHtml(review, carried);
    m.querySelector('[data-role="cfgc-intro"]').innerHTML = intro;
    m.querySelector('[data-role="cfgc-body"]').innerHTML =
      section('Needs your OK', 'Nothing here is written until you approve it.', review)
      + section('Already in your config', 'An earlier version of CCC installed these without asking. They keep working; remove any you don\'t want.', carried)
      + section(review.length || carried.length ? 'Everything else' : 'What CCC may change', '', rest)
      + (!review.length && !carried.length && !rest.length ? '<div class="cfgc-muted">Nothing to review.</div>' : '');
    m.querySelector('[data-role="cfgc-approve-all"]').hidden = !review.length || compact;
    m.querySelector('[data-role="cfgc-save"]').hidden = compact;
    m.querySelector('[data-role="cfgc-ok"]').hidden = !compact;
    m.querySelector('[data-role="cfgc-details"]').hidden = !compact;
    m.querySelector('[data-role="cfgc-revoke-all"]').hidden = mode !== 'manage' || !items.some((i) => i.installed);
    const $err = m.querySelector('[data-role="cfgc-error"]');
    $err.classList.remove('visible');
    $err.textContent = '';
    if (focusId) {
      const el = m.querySelector('.cfgc-item[data-id="' + CSS.escape(focusId) + '"]');
      if (el) setTimeout(() => el.scrollIntoView({ block: 'nearest' }), 0);
    }
  }

  function open(opts) {
    mode = (opts && opts.mode) || 'manage';
    expanded = true;
    focusId = (opts && opts.focus) || '';
    return load().then(() => {
      if (!state) return;
      render();
      ensureModal().classList.add('open');
      const first = $modal.querySelector('.cfgc-item input, [data-role="cfgc-save"]');
      if (first) first.focus({ preventScroll: true });
    });
  }

  function close(later) {
    if (!$modal) return;
    $modal.classList.remove('open');
    if (later && mode === 'auto') snooze();
  }

  function setBusy(busy) {
    if (!$modal) return;
    $modal.querySelectorAll('.cfgc-actions button').forEach((b) => { b.disabled = busy; });
  }

  function showError(msg) {
    const $err = $modal.querySelector('[data-role="cfgc-error"]');
    $err.textContent = msg;
    $err.classList.add('visible');
  }

  function post(path, body) {
    return fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    }).then((r) => r.json().catch(() => ({ ok: false, error: 'HTTP ' + r.status })));
  }

  function save(approveAll) {
    const decisions = {};
    (state.items || []).forEach((item) => {
      if (approveAll && (approveAll === 'auto' ? due(item) : item.needs_review)) { decisions[item.id] = 'approve'; return; }
      const name = 'cfgc-' + item.id.replace(/[^a-z0-9-]/gi, '_');
      const picked = $modal.querySelector('input[name="' + CSS.escape(name) + '"]:checked');
      if (picked && picked.value) decisions[item.id] = picked.value;
    });
    const noticeOn = !!(state.notice && state.notice.pending);
    const pendingLeft = (state.items || []).some((i) => due(i) && !decisions[i.id]);
    setBusy(true);
    const steps = [];
    if (Object.keys(decisions).length) steps.push(() => post('/api/config-consent/decide', { decisions }));
    if (noticeOn) steps.push(() => post('/api/config-consent/notice-ack', {}));
    const run = steps.reduce((p, step) => p.then((acc) => step().then((res) => acc.concat([res]))), Promise.resolve([]));
    return run.then((results) => {
      const failed = [];
      results.forEach((res) => {
        Object.entries((res && res.results) || {}).forEach(([id, r]) => {
          if (!r.ok) failed.push(id + ': ' + (r.error || 'failed'));
        });
        if (res && res.ok === false && !res.results) {
          failed.push(/local-only/.test(res.error || '')
            ? 'Changing agent config is only allowed from the machine CCC runs on. Open CCC there (or via localhost) to do this.'
            : (res.error || 'request failed'));
        }
      });
      return load().then(() => {
        setBusy(false);
        if (failed.length) {
          render();
          showError(failed.join('\n'));
          return;
        }
        if (pendingLeft && mode === 'auto') snooze();
        close(false);
        const n = Object.keys(decisions).length;
        if (n && typeof window.showOpToast === 'function') {
          window.showOpToast('Saved ' + n + ' agent config choice' + (n === 1 ? '' : 's') + '.', 'success');
        }
      });
    }).catch((e) => {
      setBusy(false);
      showError(String((e && e.message) || e));
    });
  }

  function revokeAll() {
    const installed = (state.items || []).filter((i) => i.installed).map((i) => i.title);
    if (!installed.length) return;
    const ok = window.confirm('Remove everything CCC installed into your agent config?\n\n- '
      + installed.join('\n- ') + '\n\nYour other hooks and settings stay as they are. '
      + 'Live tool status and CCC skills stop working until you approve them again.');
    if (!ok) return;
    setBusy(true);
    post('/api/config-consent/revoke', {}).then((res) => load().then(() => {
      setBusy(false);
      render();
      if (!res.ok) {
        const failed = Object.entries(res.results || {}).filter(([, r]) => !r.ok)
          .map(([id, r]) => id + ': ' + (r.error || 'failed'));
        showError(failed.join('\n') || res.error || 'Could not remove everything.');
      } else if (typeof window.showOpToast === 'function') {
        window.showOpToast('Removed everything CCC installed into your agent config.', 'success');
      }
    })).catch((e) => {
      setBusy(false);
      showError(String((e && e.message) || e));
    });
  }

  function maybeAutoOpen(tries) {
    if (!state || isPopout()) return;
    const anyDue = (state.items || []).some(due) || (state.notice && state.notice.pending);
    if (!anyDue || snoozed(state) || ($modal && $modal.classList.contains('open'))) return;
    if (otherOverlayOpen()) {
      // Onboarding or another dialog is up: ask once it's gone.
      if ((tries || 0) < 200) setTimeout(() => maybeAutoOpen((tries || 0) + 1), 3000);
      return;
    }
    mode = 'auto';
    expanded = false;
    focusId = '';
    render();
    ensureModal().classList.add('open');
  }

  function wire() {
    const $pill = document.getElementById('configConsentPill');
    if ($pill && !$pill._cfgcBound) {
      $pill._cfgcBound = true;
      $pill.addEventListener('click', () => open({ mode: 'manage', focus: $pill.dataset.focus || '' }));
    }
    const $btn = document.getElementById('configConsentBtn');
    if ($btn && !$btn._cfgcBound) {
      $btn._cfgcBound = true;
      $btn.addEventListener('click', () => {
        if (typeof window._cccCloseSettingsModal === 'function') window._cccCloseSettingsModal();
        open({ mode: 'manage' });
      });
    }
  }

  function boot() {
    wire();
    load().then(() => maybeAutoOpen(0));
    setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  }

  window.cccConfigConsent = { open, load, state: () => state };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
