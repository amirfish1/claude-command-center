/*
 * First magic task — a novice's first real agent run.
 *
 * Backend: /api/onboarding/first-task (ccc_server/first_task.py) creates
 * ~/CCC-Playground and runs one of three starter tasks through a headless
 * agent session. This component renders the picker, live progress, and the
 * "$0 vs $X" celebration.
 *
 * Public API (for the onboarding shell lane and standalone use):
 *   window.cccFirstTask.open()           -> modal overlay
 *   window.cccFirstTask.renderInto(el)   -> embedded mode (no overlay)
 *   window.cccFirstTask.status()         -> promise of the status payload
 *   window.cccFirstTask.close()          -> hide the overlay (job keeps running)
 *   window.cccFirstTask.isOpen()
 *
 * Standalone entry: /?first-task=1 opens the modal. /?onboarding=1 also opens
 * it, but only if the richer onboarding shell (window.cccOnboarding) never
 * shows up — a graceful fallback while lanes land separately.
 *
 * Optional integrations, all probed defensively:
 *   window.cccFx.{play,confetti,countUp,reducedMotion,muted}  (fx kit lane)
 *   window.cccNotify                                          (notify lane)
 */
(function () {
  'use strict';
  if (window.cccFirstTask) return;

  const API = '/api/onboarding/first-task';
  const Z_BASE = 100010;
  const POLL_MS = 800;
  const CONFETTI_COLORS = ['#8250df', '#39d2c0', '#3fb950', '#d29922', '#f778ba', '#58a6ff'];

  let $overlay = null;   // backdrop element when modal
  let $host = null;      // the .ft-modal or embed container
  let embedded = false;
  let statusData = null;
  let view = 'picker';   // picker | running | done | error
  let job = null;        // current/last job snapshot
  let pollTimer = null;
  let elapsedTimer = null;
  let celebrated = false;
  let cssLoaded = false;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function loadCss() {
    if (cssLoaded) return;
    cssLoaded = true;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/static/first-task.css';
    document.head.appendChild(link);
  }

  function reducedMotion() {
    if (window.cccFx && typeof window.cccFx.reducedMotion === 'function') {
      try { return !!window.cccFx.reducedMotion(); } catch (_) {}
    }
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  function play(name) {
    try {
      if (window.cccFx && typeof window.cccFx.play === 'function') window.cccFx.play(name);
    } catch (_) {}
  }

  function countUp(el, to, opts) {
    if (!el) return;
    if (window.cccFx && typeof window.cccFx.countUp === 'function') {
      try { window.cccFx.countUp(el, to, opts || {}); return; } catch (_) {}
    }
    if (reducedMotion() || !(to > 0)) {
      el.textContent = fmtUsd(to);
      return;
    }
    const start = performance.now();
    const dur = (opts && opts.duration) || 1100;
    function tick(now) {
      const k = Math.min(1, (now - start) / dur);
      const eased = 1 - Math.pow(1 - k, 3);
      el.textContent = fmtUsd(to * eased);
      if (k < 1) requestAnimationFrame(tick);
      else el.textContent = fmtUsd(to);
    }
    requestAnimationFrame(tick);
  }

  function confetti() {
    if (reducedMotion()) return;
    if (window.cccFx && typeof window.cccFx.confetti === 'function') {
      try { window.cccFx.confetti({ spread: 90 }); return; } catch (_) {}
    }
    // Fallback: a handful of CSS-animated pieces, self-cleaning.
    for (let i = 0; i < 42; i += 1) {
      const piece = document.createElement('div');
      piece.className = 'ft-confetti';
      piece.style.left = (Math.random() * 100) + 'vw';
      piece.style.background = CONFETTI_COLORS[i % CONFETTI_COLORS.length];
      piece.style.animationDuration = (1.6 + Math.random() * 1.6) + 's';
      piece.style.animationDelay = (Math.random() * 0.5) + 's';
      piece.style.transform = 'rotate(' + (Math.random() * 360) + 'deg)';
      document.body.appendChild(piece);
      setTimeout(() => piece.remove(), 4200);
    }
  }

  function fmtUsd(v) {
    const n = Number(v) || 0;
    if (n <= 0) return '$0';
    if (n < 0.01) return '< $0.01';
    return '$' + n.toFixed(2);
  }

  function fmtSecs(s) {
    s = Math.max(0, Math.round(s));
    const m = Math.floor(s / 60);
    return m ? m + 'm ' + (s % 60) + 's' : s + 's';
  }

  function api(path, opts) {
    return fetch(path, opts).then((r) => r.json().catch(() => ({})).then((j) => ({ ok: r.ok, status: r.status, json: j })));
  }

  function status() {
    return api(API).then((r) => r.json);
  }

  // ---------------- views ----------------

  function taskEmoji(id) {
    return { 'hello-3-langs': '🌍', 'fix-failing-test': '🔧', 'dark-mode': '🌙' }[id] || '✨';
  }

  function renderPicker() {
    view = 'picker';
    const rt = (statusData && statusData.runtime) || {};
    const tasks = (statusData && statusData.tasks) || [];
    const playground = (statusData && statusData.playground) || {};
    const pill = rt.free_ready
      ? '<span class="ft-runtime-pill ft-free">Runs free · $0</span>'
      : '<span class="ft-runtime-pill">Uses your Claude sign-in</span>';
    const cards = tasks.map((t) => (
      '<button type="button" class="ft-card" data-task="' + esc(t.id) + '"' +
      (rt.claude_installed === false ? ' disabled' : '') + '>' +
      '<span class="ft-card-emoji">' + taskEmoji(t.id) + '</span>' +
      '<span class="ft-card-title">' + esc(t.title) + '</span>' +
      '<span class="ft-card-blurb">' + esc(t.blurb) + '</span>' +
      '<span class="ft-card-meta">' +
      (t.done ? '<span class="ft-card-done">Done ✓</span> · ' : '') +
      '~' + esc(fmtSecs(t.est_seconds)) + '</span>' +
      '</button>'
    )).join('');
    const notice = rt.claude_installed === false
      ? '<div class="ft-notice">Claude Code is not installed yet. Finish the install steps first, then come back for your first task.</div>'
      : '';
    $host.innerHTML =
      pill +
      '<button type="button" class="ft-close" aria-label="Close">&times;</button>' +
      '<p class="ft-kicker">Moment zero</p>' +
      '<h2 class="ft-title">Watch your agent do its first real task</h2>' +
      '<p class="ft-sub">Pick one. A tiny website called <strong>CCC-Playground</strong> ' +
      'gets created for you, and your agent edits it while you watch.</p>' +
      '<div class="ft-cards">' + cards + '</div>' +
      notice +
      (playground.path
        ? '<div class="ft-playground-note">Lives in <code>' + esc(playground.path) + '</code></div>'
        : '');
    wireClose();
    $host.querySelectorAll('.ft-card').forEach((btn) => {
      btn.addEventListener('click', () => startTask(btn.getAttribute('data-task')));
    });
  }

  function renderRunning() {
    view = 'running';
    const j = job || {};
    $host.innerHTML =
      '<span class="ft-runtime-pill' + (j.runtime === 'free' ? ' ft-free' : '') + '">' +
      (j.runtime === 'free' ? 'Free · $0' : 'Your plan') + '</span>' +
      '<button type="button" class="ft-close" aria-label="Close">&times;</button>' +
      '<p class="ft-kicker">Your agent is on it</p>' +
      '<div class="ft-run-head">' +
      '<h2 class="ft-run-title">' + esc(j.task_title || 'Working') + '</h2>' +
      '<span class="ft-elapsed" id="ftElapsed"></span>' +
      '</div>' +
      '<div class="ft-progress"><div class="ft-progress-fill" id="ftProgressFill" style="width:4%"></div></div>' +
      '<div class="ft-feed" id="ftFeed" aria-live="polite"></div>' +
      '<div class="ft-actions">' +
      '<button type="button" class="ft-btn ft-btn-ghost" id="ftCancel">Cancel</button>' +
      '<span class="ft-card-meta">You can close this and come back. The run keeps going.</span>' +
      '</div>';
    wireClose();
    const cancel = document.getElementById('ftCancel');
    if (cancel) {
      cancel.addEventListener('click', () => {
        api(API + '/cancel', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ job_id: j.job_id }),
        }).finally(() => { refreshStatusThen(renderPicker); });
      });
    }
    renderLines(j);
    startElapsed();
  }

  function renderLines(j) {
    const feed = document.getElementById('ftFeed');
    if (!feed) return;
    const lines = (j && j.lines) || [];
    feed.innerHTML = lines.map((l) => {
      const icon = { say: '💬', tool: '⚙️', info: '·', error: '⚠️' }[l.kind] || '·';
      return '<div class="ft-line ft-line-' + esc(l.kind) + '">' +
        '<span class="ft-line-icon">' + icon + '</span>' +
        '<span class="ft-line-text">' + esc(l.text) + '</span></div>';
    }).join('');
    feed.scrollTop = feed.scrollHeight;
    const fill = document.getElementById('ftProgressFill');
    if (fill && j) fill.style.width = Math.max(4, Math.round((j.progress || 0) * 100)) + '%';
  }

  function startElapsed() {
    stopElapsed();
    const el = document.getElementById('ftElapsed');
    if (!el || !job || !job.started_at) return;
    const tick = () => {
      el.textContent = fmtSecs(Date.now() / 1000 - job.started_at) + ' elapsed';
    };
    tick();
    elapsedTimer = setInterval(tick, 1000);
  }

  function stopElapsed() {
    if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
  }

  function savingsCopy(j) {
    const usage = (j && j.usage) || {};
    const value = Number(usage.api_value_usd) || 0;
    if (j.runtime === 'free') {
      return {
        headline: 'This run cost <strong>$0</strong>.',
        sub: value > 0
          ? 'At API prices it would have cost <span id="ftValue">' + fmtUsd(value) + '</span>. Free model, real work.'
          : 'Free model, real work.',
        value: value,
      };
    }
    return {
      headline: 'Your agent just did real work for you.',
      sub: value > 0
        ? 'Work like this costs about <span id="ftValue">' + fmtUsd(value) + '</span> at API prices. On your plan it added $0.'
        : 'All part of your plan.',
      value: value,
    };
  }

  function renderDone() {
    view = 'done';
    stopElapsed();
    const j = job || {};
    const res = j.result || {};
    const copy = savingsCopy(j);
    const checks = (res.checks || []).map((c) => (
      '<li class="' + (c.ok ? 'ft-check-ok' : 'ft-check-fail') + '">' +
      '<span class="ft-check-mark">' + (c.ok ? '✓' : '·') + '</span>' +
      '<span>' + esc(c.label) + (c.ok ? '' : (c.detail ? ' (' + esc(c.detail) + ')' : '')) + '</span></li>'
    )).join('');
    const secs = j.started_at && j.finished_at ? Math.round(j.finished_at - j.started_at) : 0;
    $host.innerHTML =
      '<button type="button" class="ft-close" aria-label="Close">&times;</button>' +
      '<div class="ft-celebrate">' +
      '<div class="ft-burst">🎉</div>' +
      '<h2 class="ft-done-title">Your agent just shipped code</h2>' +
      '<div class="ft-savings">' + copy.headline +
      '<span class="ft-savings-sub">' + copy.sub +
      (secs ? ' · finished in ' + esc(fmtSecs(secs)) : '') + '</span></div>' +
      (res.verified === false
        ? '<p class="ft-savings-sub">Checks could not confirm everything. Open the page and take a look.</p>'
        : '') +
      (checks ? '<ul class="ft-checks">' + checks + '</ul>' : '') +
      '<div class="ft-done-actions">' +
      '<button type="button" class="ft-btn ft-btn-primary" id="ftOpenPage">See your page</button>' +
      '<button type="button" class="ft-btn" id="ftAnother">Run another task</button>' +
      (!embedded ? '<button type="button" class="ft-btn ft-btn-ghost" id="ftDone">Done</button>' : '') +
      '</div></div>';
    wireClose();
    const valueEl = document.getElementById('ftValue');
    if (valueEl && copy.value > 0) {
      countUp(valueEl, copy.value, { format: fmtUsd, duration: 1100 });
    }
    const openBtn = document.getElementById('ftOpenPage');
    if (openBtn) {
      openBtn.addEventListener('click', () => {
        openBtn.disabled = true;
        api(API + '/open', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ job_id: j.job_id }),
        }).then((r) => {
          openBtn.disabled = false;
          if (!r.ok || !r.json.ok) {
            openBtn.textContent = 'Open ' + ((j.playground || '~/CCC-Playground') + '/index.html');
          } else {
            openBtn.textContent = 'Opened ✓';
            play('coin');
          }
        }).catch(() => { openBtn.disabled = false; });
      });
    }
    const another = document.getElementById('ftAnother');
    if (another) another.addEventListener('click', () => refreshStatusThen(renderPicker));
    const doneBtn = document.getElementById('ftDone');
    if (doneBtn) doneBtn.addEventListener('click', close);
    if (!celebrated) {
      celebrated = true;
      confetti();
      play('success');
      try {
        if (window.cccNotify && typeof window.cccNotify === 'function') {
          const usage = (j.usage) || {};
          const v = Number(usage.api_value_usd) || 0;
          window.cccNotify({
            title: 'First task done',
            body: j.runtime === 'free'
              ? 'Your agent finished "' + (j.task_title || 'the task') + '" for $0' + (v ? ' (worth ' + fmtUsd(v) + ')' : '') + '.'
              : 'Your agent finished "' + (j.task_title || 'the task') + '".',
            kind: 'success',
          });
        }
      } catch (_) {}
    }
  }

  function renderError(errText) {
    view = 'error';
    stopElapsed();
    const msg = (job && job.error) || errText || 'Something went sideways.';
    const lines = (job && job.lines) || [];
    const feed = lines.length
      ? '<div class="ft-feed" id="ftFeed" style="height:160px;margin-top:14px;text-align:left"></div>'
      : '';
    $host.innerHTML =
      '<button type="button" class="ft-close" aria-label="Close">&times;</button>' +
      '<div class="ft-error-box">' +
      '<div class="ft-error-emoji">🛠️</div>' +
      '<h2 class="ft-done-title">That run hit a snag</h2>' +
      '<p>' + esc(msg) + '</p>' +
      feed +
      '<div class="ft-done-actions">' +
      '<button type="button" class="ft-btn ft-btn-primary" id="ftRetry">Try again</button>' +
      '<button type="button" class="ft-btn ft-btn-ghost" id="ftBack">Back</button>' +
      '</div></div>';
    wireClose();
    if (feed) renderLines(job);
    const retry = document.getElementById('ftRetry');
    if (retry && job) {
      retry.addEventListener('click', () => startTask(job.task_id));
    }
    const back = document.getElementById('ftBack');
    if (back) back.addEventListener('click', () => refreshStatusThen(renderPicker));
  }

  // ---------------- plumbing ----------------

  function wireClose() {
    const btn = $host && $host.querySelector('.ft-close');
    if (btn && !embedded) btn.addEventListener('click', close);
    if (btn && embedded) btn.style.display = 'none';
  }

  function refreshStatusThen(next) {
    api(API).then((r) => {
      if (r.ok && r.json && r.json.ok) statusData = r.json;
    }).catch(() => {}).finally(() => { if ($host) next(); });
  }

  function startTask(taskId) {
    play('step');
    view = 'running';
    job = { task_id: taskId, task_title: 'Starting…', status: 'running', progress: 0.02, lines: [], started_at: Date.now() / 1000, runtime: null };
    renderRunning();
    api(API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId }),
    }).then((r) => {
      if (r.ok && r.json.ok && r.json.job) {
        job = r.json.job;
        if (view === 'running') renderRunning();
        pollLoop();
      } else {
        job = { status: 'error', error: (r.json && r.json.error) || 'could not start the task' };
        renderError();
      }
    }).catch(() => {
      job = { status: 'error', error: 'could not reach the server' };
      renderError();
    });
  }

  function pollLoop() {
    stopPolling();
    const tick = () => {
      if (!job || !job.job_id) return;
      api(API + '/' + encodeURIComponent(job.job_id)).then((r) => {
        if (!r.ok || !r.json.ok) return;
        job = r.json.job;
        if (view === 'running') renderLines(job);
        if (job.status === 'done') {
          stopPolling();
          renderDone();
        } else if (job.status === 'error' || job.status === 'cancelled') {
          stopPolling();
          renderError();
        } else {
          pollTimer = setTimeout(tick, POLL_MS);
        }
      }).catch(() => { pollTimer = setTimeout(tick, POLL_MS * 3); });
    };
    pollTimer = setTimeout(tick, POLL_MS);
  }

  function stopPolling() {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  }

  // ---------------- public API ----------------

  function open(opts) {
    loadCss();
    if ($overlay) { $overlay.style.display = ''; return; }
    embedded = false;
    celebrated = false;
    $overlay = document.createElement('div');
    $overlay.className = 'ft-backdrop';
    $overlay.style.zIndex = Z_BASE;
    $host = document.createElement('div');
    $host.className = 'ft-modal';
    $host.setAttribute('role', 'dialog');
    $host.setAttribute('aria-modal', 'true');
    $host.setAttribute('aria-label', 'Your first task');
    $overlay.appendChild($host);
    $overlay.addEventListener('click', (e) => { if (e.target === $overlay) close(); });
    document.body.appendChild($overlay);
    document.addEventListener('keydown', onKeydown);
    api(API).then((r) => {
      if (r.ok && r.json && r.json.ok) {
        statusData = r.json;
        if (statusData.active_job) {
          job = statusData.active_job;
          renderRunning();
          pollLoop();
          return;
        }
        if (statusData.last_job && statusData.last_job.status === 'done' && !(opts && opts.fresh)) {
          // Reopening after a finished run shows the celebration again briefly,
          // not forever: honor explicit fresh opens with the picker.
          job = statusData.last_job;
          renderDone();
          return;
        }
      }
      renderPicker();
    }).catch(() => renderPicker());
  }

  function renderInto(el) {
    if (!el) return;
    loadCss();
    embedded = true;
    $host = el;
    $host.classList.add('ft-modal');
    api(API).then((r) => {
      if (r.ok && r.json && r.json.ok) statusData = r.json;
      renderPicker();
    }).catch(() => renderPicker());
  }

  function onKeydown(e) {
    if (e.key === 'Escape' && $overlay) close();
  }

  function close() {
    stopPolling();
    stopElapsed();
    if ($overlay) {
      $overlay.remove();
      $overlay = null;
      $host = null;
      document.removeEventListener('keydown', onKeydown);
    }
  }

  function isOpen() { return !!$overlay; }

  window.cccFirstTask = { open, close, renderInto, status, isOpen };

  // ---------------- entry points ----------------

  function boot() {
    let qs;
    try { qs = new URLSearchParams(location.search); } catch (_) { return; }
    if (qs.has('first-task')) {
      open();
      return;
    }
    if (qs.has('onboarding')) {
      // The onboarding shell lane owns ?onboarding=1. If it is not merged yet,
      // offer the first task directly rather than stranding a brand-new user.
      setTimeout(() => {
        if (!window.cccOnboarding && !$overlay) open();
      }, 2500);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
