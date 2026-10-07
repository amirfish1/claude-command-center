/* Moment Zero — the first-run onboarding shell.
 *
 * A full-screen, cinematic welcome for people who have never run a coding
 * agent: animated logo + typed headline, then a step machine driven by
 * /api/setup/plan + /api/setup/jobs, the free-key wizard (L03's component
 * when present, a compact built-in fallback otherwise), the first magic
 * task (L09), and a "$0 vs $X" finale.
 *
 * Public surface (the L07 contract):
 *   - route `/?onboarding=1` opens the shell for anyone
 *   - window.cccOnboarding.open(opts) / .close() / .isOpen()
 *   - window.cccOnboarding.claimFirstRun() — app.js asks this before showing
 *     the old engines first-run; resolves true when Moment Zero took over.
 *
 * Every sibling-lane dependency is optional: a 404 or a missing global
 * degrades that step to a friendly card instead of breaking the flow.
 */
(function () {
  'use strict';
  if (window.cccOnboarding) return;

  const LS_ONBOARDED = 'ccc-onboarded';
  const LS_JOB = 'ccc-onboarding-job';
  const LS_FIRST_TASK = 'ccc-onboarding-first-task';
  const LS_ENG_DONE = 'ccc-engines-first-run-done';
  const LS_SOUNDS = 'ccc-sounds-enabled';
  const POLL_MS = 900;
  const Z_BASE = 100300;

  const isPopout = (function () {
    try {
      return window.__CCC_CONVERSATION_POPOUT__ === true
        || new URLSearchParams(window.location.search || '').get('ccc_popout') === 'conversation';
    } catch (_) { return false; }
  })();

  function lsGet(k) { try { return window.localStorage.getItem(k); } catch (_) { return null; } }
  function lsSet(k, v) { try { window.localStorage.setItem(k, v); } catch (_) {} }
  function lsDel(k) { try { window.localStorage.removeItem(k); } catch (_) {} }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function reducedMotion() {
    try {
      if (window.cccFx && typeof window.cccFx.reducedMotion === 'function') {
        return !!window.cccFx.reducedMotion();
      }
    } catch (_) {}
    try {
      return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    } catch (_) { return false; }
  }

  function muted() {
    try {
      if (window.cccFx && typeof window.cccFx.muted === 'function') return !!window.cccFx.muted();
    } catch (_) {}
    return lsGet(LS_SOUNDS) === '0' || lsGet(LS_SOUNDS) === 'false';
  }

  function sfx(name) {
    if (muted()) return;
    try {
      if (window.cccFx && typeof window.cccFx.play === 'function') window.cccFx.play(name);
    } catch (_) {}
  }

  function confetti(opts) {
    if (reducedMotion()) return;
    try {
      if (window.cccFx && typeof window.cccFx.confetti === 'function') {
        window.cccFx.confetti(opts);
        return;
      }
    } catch (_) {}
    // Fallback: a tiny DOM burst so the finale still feels alive on builds
    // where the fx kit has not landed yet.
    const host = document.getElementById('cccMomentZero');
    if (!host) return;
    const burst = document.createElement('div');
    burst.className = 'mz-confetti-fallback';
    const colors = ['#e07655', '#ffd1a6', '#3fb950', '#39d2c0', '#bc8cff', '#58a6ff'];
    for (let i = 0; i < 28; i++) {
      const p = document.createElement('i');
      p.style.setProperty('--mz-x', (Math.random() * 2 - 1).toFixed(2));
      p.style.setProperty('--mz-r', Math.floor(Math.random() * 540) + 'deg');
      p.style.setProperty('--mz-d', (0.9 + Math.random() * 0.8).toFixed(2) + 's');
      p.style.background = colors[i % colors.length];
      burst.appendChild(p);
    }
    host.appendChild(burst);
    setTimeout(() => { if (burst.parentNode) burst.parentNode.removeChild(burst); }, 2600);
  }

  function countUp(el, to) {
    try {
      if (window.cccFx && typeof window.cccFx.countUp === 'function') {
        window.cccFx.countUp(el, to, { prefix: '$', decimals: 2 });
        return;
      }
    } catch (_) {}
    el.textContent = '$' + Number(to || 0).toFixed(2);
  }

  // ── Driver: every sibling-lane call lives here so tests can swap it ──
  async function api(path, opts) {
    const res = await fetch(path, Object.assign({ cache: 'no-store' }, opts || {}));
    let data = null;
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) {
      const err = new Error((data && (data.error || data.detail)) || ('HTTP ' + res.status));
      err.status = res.status;
      throw err;
    }
    return data;
  }
  const realDriver = {
    getPlan: () => api('/api/setup/plan?refresh=1'),
    runSteps: (ids) => api('/api/setup/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ steps: ids }),
    }),
    pollJob: (id) => api('/api/setup/jobs/' + encodeURIComponent(id)).catch((err) => {
      if (err.status !== 404) throw err;
      return api('/api/free-router/jobs/' + encodeURIComponent(id));
    }),
    routerStatus: () => api('/api/free-router/status'),
    installRouter: () => api('/api/free-router/install', { method: 'POST' }),
    startRouter: () => api('/api/free-router/start', { method: 'POST' }),
    pollFirstTask: (id) => api('/api/onboarding/first-task/' + encodeURIComponent(id)).then((r) => r.job),
    detectedRouters: () => api('/api/free-router/detected'),
    providers: () => api('/api/free-router/providers'),
    submitKey: (platform, key) => api('/api/free-router/keys', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(key ? { platform, key } : { platform }),
    }),
    firstTask: (taskId) => api('/api/onboarding/first-task', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId, runtime: 'free' }),
    }).then((r) => r.job),
    savings: () => api('/api/savings?range=today'),
    freshInstall: () => api('/api/onboarding/moment-zero'),
  };
  let driver = realDriver;

  // ── Per-step novice copy. The plan's own label wins when present. ──
  const STEP_META = {
    clt: {
      title: 'Apple developer tools',
      blurb: 'The toolbox macOS uses to build software. One click, Apple does the rest.',
      running: 'Installing developer tools. If a system popup appears, click Install.',
    },
    git: { title: 'Git', blurb: 'Keeps every version of the work safe.', running: 'Setting up Git.' },
    python: { title: 'Python', blurb: 'The engine under Command Center itself.', running: 'Setting up Python.' },
    node: { title: 'Node.js', blurb: 'Runs your free-model router, right on this Mac.', running: 'Setting up Node.js.' },
    claude_cli: { title: 'Claude Code', blurb: 'The agent that does the actual coding.', running: 'Installing Claude Code.' },
    gh: { title: 'GitHub CLI', blurb: 'Lets your agents open pull requests. Optional but handy.', running: 'Installing the GitHub CLI.' },
    free_router: {
      title: 'Your free-model router',
      blurb: 'A tiny local switchboard that sends runs to $0 models. This is where the magic happens.',
      running: 'Building your free-model router.',
    },
    free_key: { title: 'A free API key', blurb: 'Unlocks better free models. Still $0.', running: 'Connecting your free key.' },
    first_task: { title: 'Your first task', blurb: 'Watch an agent ship something real.', running: 'Your agent is working.' },
  };

  const FIRST_TASKS = [
    {
      id: 'hello-3-langs',
      title: 'Say hello in 3 languages',
      desc: 'A tiny web page that greets the world. The easiest first ship.',
    },
    {
      id: 'fix-failing-test',
      title: 'Fix the failing test',
      desc: 'A real bug, a real fix, a green checkmark. The daily driver move.',
    },
    {
      id: 'dark-mode',
      title: 'Add a dark mode toggle',
      desc: 'A small feature with a real UI change you can click.',
    },
  ];

  // ── Shell state ──
  const S = {
    open: false,
    scene: 'welcome',
    plan: null,           // {steps:[{id,label,status,detail,needs_consent,est_seconds}]}
    planFailed: false,    // /api/setup/plan unavailable in this build
    planSupported: true,
    jobId: null,
    jobTimer: null,
    jobRunningStep: null,
    detectedFetched: false,
    consented: {},        // step id -> true
    running: false,
    savedUsd: null,
    firstTaskDone: false,
    firstTaskJobId: null,
    firstTaskTimer: null,
    force: false,
    prevFocus: null,
    providers: null,
    detected: null,
    wizardMounted: false,
  };

  let $root = null;

  // ── DOM helpers ──
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function ensureRoot() {
    if ($root) return $root;
    $root = el('div', 'mz-overlay upd-overlay');
    $root.id = 'cccMomentZero';
    $root.setAttribute('role', 'dialog');
    $root.setAttribute('aria-modal', 'true');
    $root.setAttribute('aria-label', 'Welcome setup');
    $root.style.zIndex = String(Z_BASE);
    $root.hidden = true;
    document.body.appendChild($root);
    // Keydown lives on document (capture): focus can legitimately sit on
    // body between scenes, and an overlay-scoped listener would miss Escape.
    document.addEventListener('keydown', onKeydown, true);
    return $root;
  }

  // Focus only after the overlay is displayed; a display:none focus() no-ops.
  function focusSoon(node) {
    requestAnimationFrame(() => {
      try { if (S.open && node && node.isConnected) node.focus({ preventScroll: true }); } catch (_) {}
    });
  }

  function shell(inner) {
    const r = ensureRoot();
    r.textContent = '';
    const aurora = el('div', 'mz-aurora');
    aurora.setAttribute('aria-hidden', 'true');
    aurora.innerHTML = '<i class="mz-blob mz-blob-a"></i><i class="mz-blob mz-blob-b"></i><i class="mz-blob mz-blob-c"></i>';
    r.appendChild(aurora);

    const top = el('div', 'mz-top');
    top.appendChild(muteBtn());
    const skip = el('button', 'mz-skip', 'Skip setup');
    skip.type = 'button';
    skip.addEventListener('click', () => {
      sfx('whoosh');
      markDone();
      close();
    });
    if (S.scene !== 'finale' && S.scene !== 'welcome') top.appendChild(skip);
    r.appendChild(top);

    const stage = el('div', 'mz-stage');
    stage.appendChild(inner);
    r.appendChild(stage);
    return r;
  }

  function muteBtn() {
    const b = el('button', 'mz-mute');
    b.type = 'button';
    const paint = () => {
      const m = muted();
      b.setAttribute('aria-pressed', m ? 'true' : 'false');
      b.setAttribute('aria-label', m ? 'Unmute sounds' : 'Mute sounds');
      b.innerHTML = m
        ? '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5 6 9H2v6h4l5 4V5z"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>'
        : '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5 6 9H2v6h4l5 4V5z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M19 5a9 9 0 0 1 0 14"/></svg>';
    };
    paint();
    b.addEventListener('click', () => {
      const next = !muted();
      lsSet(LS_SOUNDS, next ? '0' : '1');
      paint();
      if (!next) sfx('step');
    });
    return b;
  }

  function logoSvg() {
    // The CCC constellation mark, drawn in: triangle edges draw, nodes pop.
    return '<svg class="mz-logo" viewBox="0 0 200 200" role="img" aria-label="Command Center">'
      + '<defs><radialGradient id="mzGlow" cx="50%" cy="55%" r="65%">'
      + '<stop offset="0%" stop-color="#ff8855" stop-opacity="0.55"/>'
      + '<stop offset="100%" stop-color="#ff5530" stop-opacity="0"/></radialGradient>'
      + '<radialGradient id="mzNode" cx="35%" cy="30%" r="80%">'
      + '<stop offset="0%" stop-color="#ffd1a6"/><stop offset="60%" stop-color="#e07655"/>'
      + '<stop offset="100%" stop-color="#a04830"/></radialGradient></defs>'
      + '<circle class="mz-logo-glow" cx="100" cy="112" r="66" fill="url(#mzGlow)"/>'
      + '<g class="mz-logo-lines" stroke="#e07655" stroke-width="3" stroke-linecap="round" fill="none">'
      + '<line class="mz-edge mz-e1" x1="100" y1="54" x2="54" y2="146"/>'
      + '<line class="mz-edge mz-e2" x1="54" y1="146" x2="146" y2="146"/>'
      + '<line class="mz-edge mz-e3" x1="146" y1="146" x2="100" y2="54"/></g>'
      + '<circle class="mz-node mz-n1" cx="100" cy="54" r="11" fill="url(#mzNode)"/>'
      + '<circle class="mz-node mz-n2" cx="54" cy="146" r="11" fill="url(#mzNode)"/>'
      + '<circle class="mz-node mz-n3" cx="146" cy="146" r="11" fill="url(#mzNode)"/>'
      + '<circle class="mz-node mz-n0" cx="100" cy="112" r="7.5" fill="#ffe2b8"/></svg>';
  }

  // ── Scene: Welcome ──
  function renderWelcome() {
    const card = el('section', 'mz-card mz-welcome');
    card.innerHTML = logoSvg();
    const h = el('h1', 'mz-headline');
    h.setAttribute('aria-label', 'Your own AI dev team. Free.');
    const typed = el('span', 'mz-typed');
    const caret = el('span', 'mz-caret');
    caret.setAttribute('aria-hidden', 'true');
    h.appendChild(typed);
    h.appendChild(caret);
    card.appendChild(h);
    const sub = el('p', 'mz-sub', 'Command Center hires a team of coding agents and runs them on $0 models. We will install everything together. About five minutes.');
    card.appendChild(sub);
    const actions = el('div', 'mz-actions');
    const go = el('button', 'mz-btn mz-btn-primary', 'Set up my team');
    go.type = 'button';
    go.addEventListener('click', () => { sfx('whoosh'); goSteps(); });
    const ghost = el('button', 'mz-btn mz-btn-ghost', 'I will explore on my own');
    ghost.type = 'button';
    ghost.addEventListener('click', () => { sfx('whoosh'); markDone(); close(); });
    actions.appendChild(go);
    actions.appendChild(ghost);
    card.appendChild(actions);
    const hint = el('p', 'mz-kbd-hint', 'Press Enter to begin');
    card.appendChild(hint);

    shell(card);
    focusSoon(go);
    sfx('welcome');

    const line1 = 'Your own AI dev team.';
    const line2 = ' Free.';
    if (reducedMotion()) {
      typed.innerHTML = esc(line1) + '<span class="mz-free">' + esc(line2) + '</span>';
      return;
    }
    let i = 0;
    const full = line1 + line2;
    (function tick() {
      if (!S.open || S.scene !== 'welcome') return;
      i += 1;
      const done = full.slice(0, i);
      const a = done.slice(0, Math.min(done.length, line1.length));
      const b = done.length > line1.length ? done.slice(line1.length) : '';
      typed.innerHTML = esc(a) + (b ? '<span class="mz-free">' + esc(b) + '</span>' : '');
      if (i < full.length) setTimeout(tick, 34 + Math.random() * 40);
    })();
  }

  // ── Scene: Steps ──
  function goSteps() {
    S.scene = 'steps';
    sfx('step');
    renderSteps();
    loadPlan();
  }

  function planSteps() {
    return (S.plan && Array.isArray(S.plan.steps)) ? S.plan.steps : [];
  }

  function stepState(st) {
    const raw = st && st.status;
    if (raw === 'ok') return 'done';
    if (raw === 'error') return 'error';
    if (S.consented[st.id] || !st.needs_consent) return 'pending';
    return 'consent';
  }

  function renderSteps() {
    const card = el('section', 'mz-card mz-steps-card');
    const head = el('div', 'mz-steps-head');
    head.appendChild(el('div', 'mz-eyebrow', 'Setup'));
    head.appendChild(el('h2', 'mz-title', 'Getting your team ready'));
    head.appendChild(el('p', 'mz-sub mz-sub-left',
      'One click per step. Everything installs on your Mac, nothing leaves it.'));
    card.appendChild(head);

    const list = el('div', 'mz-step-list');
    list.id = 'mzStepList';
    card.appendChild(list);
    const live = el('div', 'mz-live');
    live.id = 'mzLive';
    live.setAttribute('aria-live', 'polite');
    card.appendChild(live);

    shell(card);
    renderStepRows();

    const skipWrap = el('div', 'mz-foot');
    const later = el('button', 'mz-linklike', 'Finish this later from Settings');
    later.type = 'button';
    later.addEventListener('click', () => { markDone(); close(); });
    skipWrap.appendChild(later);
    card.appendChild(skipWrap);
  }

  function renderStepRows() {
    const list = document.getElementById('mzStepList');
    if (!list) return;
    list.textContent = '';
    const steps = planSteps();
    if (!steps.length) {
      if (S.planFailed) {
        list.appendChild(renderPlanFallback());
      } else {
        const d = el('div', 'mz-loading');
        d.innerHTML = '<span class="mz-spinner" aria-hidden="true"></span> Checking what your Mac already has…';
        list.appendChild(d);
      }
      return;
    }
    const runningId = S.jobRunningStep || null;
    steps.forEach((st) => {
      const row = el('div', 'mz-step-row');
      const state = runningId === st.id ? 'running' : stepState(st);
      row.dataset.stepId = st.id;
      row.classList.add('is-' + state);
      const icon = el('span', 'mz-step-icon');
      icon.setAttribute('aria-hidden', 'true');
      icon.innerHTML = state === 'done' ? '<svg viewBox="0 0 16 16" width="14" height="14"><path d="M2.5 8.5l3.5 3.5 7-8" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        : state === 'running' ? '<span class="mz-spinner"></span>'
        : state === 'error' ? '<svg viewBox="0 0 16 16" width="14" height="14"><path d="M8 4v5M8 12.2v.3" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>'
        : '<span class="mz-dot"></span>';
      row.appendChild(icon);

      const main = el('div', 'mz-step-main');
      const meta = STEP_META[st.id] || {};
      const title = el('div', 'mz-step-title', meta.title || st.label || st.id);
      main.appendChild(title);
      const blurb = meta.blurb || st.detail || '';
      if (blurb) main.appendChild(el('div', 'mz-step-desc', blurb));
      if (st.detail && meta.blurb) main.appendChild(el('div', 'mz-step-detail', st.detail));
      if (state === 'running' && meta.running) {
        main.appendChild(el('div', 'mz-step-running', meta.running));
      }
      row.appendChild(main);

      const side = el('div', 'mz-step-side');
      const est = st.est_seconds && state !== 'done' ? '~' + Math.max(1, Math.round(st.est_seconds / 60)) + ' min' : '';
      if (state === 'done') side.appendChild(el('span', 'mz-step-state is-ok', 'Done'));
      else if (state === 'running') side.appendChild(el('span', 'mz-step-state is-run', 'Working'));
      else if (state === 'error') side.appendChild(el('span', 'mz-step-state is-err', 'Needs attention'));
      else if (est) side.appendChild(el('span', 'mz-step-est', est));
      row.appendChild(side);
      list.appendChild(row);
    });

    // Active detail area under the list: consent + run controls, or the
    // embedded wizard / task picker for the special steps.
    const detail = el('div', 'mz-step-detailbar');
    detail.id = 'mzStepDetail';
    list.appendChild(detail);
    renderStepDetail();
  }

  function renderPlanFallback() {
    const box = el('div', 'mz-fallback');
    box.appendChild(el('div', 'mz-fallback-title', 'Automatic setup is not in this build yet'));
    box.appendChild(el('p', 'mz-fallback-body',
      'This preview does not include the installer service. Open a terminal and run '
      + '"claude" to try your engine, or continue to the dashboard and explore.'));
    const row = el('div', 'mz-actions');
    const c = el('button', 'mz-btn mz-btn-primary', 'Continue to dashboard');
    c.type = 'button';
    c.addEventListener('click', () => { markDone(); close(); });
    const retry = el('button', 'mz-btn mz-btn-ghost', 'Check again');
    retry.type = 'button';
    retry.addEventListener('click', () => { S.planFailed = false; S.plan = null; renderStepRows(); loadPlan(); });
    row.appendChild(c);
    row.appendChild(retry);
    box.appendChild(row);
    return box;
  }

  async function loadPlan() {
    try {
      const plan = await driver.getPlan();
      S.plan = plan || { steps: [] };
      S.planFailed = false;
    } catch (e) {
      if (e && e.status === 404) { S.planFailed = true; S.plan = null; }
      else { S.planFailed = true; S.plan = null; }
    }
    if (S.scene === 'steps') {
      renderStepRows();
      maybeOfferResume();
    }
  }

  function pendingStepIds() {
    return planSteps()
      .filter((st) => stepState(st) !== 'done')
      .map((st) => st.id);
  }

  function nextActionableStep() {
    const steps = planSteps();
    return steps.find((st) => stepState(st) !== 'done') || null;
  }

  function renderStepDetail() {
    const host = document.getElementById('mzStepDetail');
    if (!host) return;
    host.textContent = '';
    if (!planSteps().length) return;

    const next = nextActionableStep();
    const taskStep = planSteps().find((st) => st.id === 'first_task');
    if (taskStep && lsGet(LS_FIRST_TASK)) { renderFirstTaskStep(host, taskStep); return; }
    if (!next) {
      // Everything the plan knows about is done.
      const done = el('div', 'mz-all-done');
      done.appendChild(el('div', 'mz-all-done-title', 'All set.'));
      const btn = el('button', 'mz-btn mz-btn-primary', 'See your first win');
      btn.type = 'button';
      btn.addEventListener('click', () => { sfx('success'); goFinale(); });
      done.appendChild(btn);
      host.appendChild(done);
      return;
    }

    if (S.jobId || S.running) {
      host.appendChild(el('div', 'mz-hint mz-loading', 'Working through the list. You can close this and come back; it keeps going.'));
      return;
    }

    if (next.id === 'free_key') { renderFreeKeyStep(host, next); return; }
    if (next.id === 'first_task') { renderFirstTaskStep(host, next); return; }

    // L20: when the router step is up, surface a router that already exists
    // on this machine so the install step can reuse it instead of cloning.
    if (next.id === 'free_router' && !S.detectedFetched) {
      S.detectedFetched = true;
      Promise.resolve()
        .then(() => driver.detectedRouters())
        .then((d) => { S.detected = Array.isArray(d) ? d : (d && d.routers) || []; })
        .catch(() => { S.detected = []; })
        .then(() => { if (S.scene === 'steps') renderStepDetail(); });
    }
    if (next.id === 'free_router' && Array.isArray(S.detected) && S.detected.length) {
      const found = S.detected[0];
      const note = el('div', 'mz-detected');
      const name = found.name || found.label || 'free-model router';
      const port = found.port || (found.base_url ? String(found.base_url).replace(/.*:(\d+).*/, '$1') : '');
      note.appendChild(el('span', 'mz-detected-dot'));
      note.appendChild(el('span', null,
        'Found ' + name + ' already running on this Mac' + (port ? ' (port ' + port + ')' : '') + '. You can connect it from Free models in Settings.'));
      host.appendChild(note);
    }

    const consent = !!next.needs_consent && !S.consented[next.id];
    const wrap = el('div', 'mz-step-cta');
    const label = consent
      ? ('Allow: ' + (STEP_META[next.id] && STEP_META[next.id].title || next.label || next.id))
      : 'Continue setup';
    const go = el('button', 'mz-btn mz-btn-primary', label);
    go.type = 'button';
    go.addEventListener('click', () => {
      if (consent) S.consented[next.id] = true;
      startRun();
    });
    wrap.appendChild(go);
    const skipStep = el('button', 'mz-linklike', 'Skip this step');
    skipStep.type = 'button';
    skipStep.addEventListener('click', () => {
      next.status = 'ok';
      S.consented[next.id] = true;
      sfx('step');
      renderStepRows();
    });
    wrap.appendChild(skipStep);
    host.appendChild(wrap);
  }

  async function startRun() {
    if (S.jobId || S.running) return;
    const next = nextActionableStep();
    const ids = pendingStepIds().filter((id) => {
      const st = planSteps().find((x) => x.id === id);
      return !st || !st.needs_consent || S.consented[id];
    });
    // free_key and first_task are interactive scenes, not runner work.
    const runnable = ids.filter((id) => id !== 'free_key' && id !== 'first_task');
    if (!runnable.length) { renderStepRows(); return; }
    S.running = true;
    renderStepDetail();
    try {
      let res;
      if (next && next.id === 'free_router') {
        const status = await driver.routerStatus();
        res = status && status.installed ? await driver.startRouter() : await driver.installRouter();
      } else {
        res = await driver.runSteps(runnable.filter((id) => id !== 'free_router'));
      }
      if (res && res.ok === false) throw new Error(res.error || 'The free engine could not start.');
      if (res && res.job_id) {
        S.jobId = res.job_id;
        lsSet(LS_JOB, res.job_id);
        pollJob();
      } else {
        // Runner answered without a job: treat as finished and re-check.
        await loadPlan();
      }
    } catch (e) {
      if (e && e.status === 404) { S.planFailed = true; }
      const live = document.getElementById('mzLive');
      if (live) live.textContent = (e && e.message) || 'The installer did not answer. Try again in a moment.';
    } finally {
      S.running = false;
    }
    if (S.scene === 'steps') renderStepRows();
  }

  async function pollJob() {
    if (!S.jobId) return;
    clearTimeout(S.jobTimer);
    try {
      const j = await driver.pollJob(S.jobId);
      S.jobRunningStep = j && j.step ? j.step : null;
      updateJobUi(j || {});
      if (j && (j.status === 'done' || j.status === 'error')) {
        const wasError = j.status === 'error';
        S.jobId = null;
        S.jobRunningStep = null;
        lsDel(LS_JOB);
        const live = document.getElementById('mzLive');
        if (live) live.textContent = '';
        sfx(wasError ? 'error' : 'success');
        await loadPlan();
        if (wasError) {
          const live = document.getElementById('mzLive');
          if (live) live.textContent = 'One step hit a snag. You can retry it, skip it, or finish setup later from Settings.';
        }
        return;
      }
    } catch (e) {
      if (e && e.status === 404) {
        // Job runner vanished (old build): stop polling, re-check the plan.
        S.jobId = null;
        S.jobRunningStep = null;
        lsDel(LS_JOB);
        await loadPlan();
        return;
      }
      // Transient failure: keep polling gently.
    }
    S.jobTimer = setTimeout(pollJob, POLL_MS);
  }

  function updateJobUi(j) {
    const live = document.getElementById('mzLive');
    if (!live) return;
    const lines = Array.isArray(j.lines) ? j.lines : [];
    const tail = lines.slice(-3).map((l) => String(l || '').trim()).filter(Boolean);
    let html = '';
    if (j.step && STEP_META[j.step] && STEP_META[j.step].running) {
      html += '<div class="mz-live-title">' + esc(STEP_META[j.step].running) + '</div>';
    }
    if (tail.length) {
      html += '<div class="mz-live-log">' + tail.map(esc).join('\n') + '</div>';
    }
    if (typeof j.progress === 'number') {
      const pct = Math.max(0, Math.min(100, Math.round(j.progress * 100)));
      html += '<div class="mz-progress"><span class="mz-progress-fill" style="width:' + pct + '%"></span></div>';
    }
    live.innerHTML = html;
    // Reflect the running step onto its row.
    const list = document.getElementById('mzStepList');
    if (list) {
      list.querySelectorAll('.mz-step-row.is-running').forEach((r) => {
        if (r.dataset.stepId !== j.step) r.classList.remove('is-running');
      });
      const row = j.step && list.querySelector('.mz-step-row[data-step-id="' + CSS.escape(j.step) + '"]');
      if (row && !row.classList.contains('is-running')) {
        row.classList.remove('is-consent', 'is-pending');
        row.classList.add('is-running');
        const icon = row.querySelector('.mz-step-icon');
        if (icon) icon.innerHTML = '<span class="mz-spinner"></span>';
      }
    }
  }

  function maybeOfferResume() {
    // If a previous visit left a running job, resume polling it.
    const saved = lsGet(LS_JOB);
    if (saved && !S.jobId) { S.jobId = saved; pollJob(); }
  }

  // ── free_key scene fragment ──
  async function renderFreeKeyStep(host, step) {
    host.textContent = '';
    const box = el('div', 'mz-keybox');
    box.appendChild(el('div', 'mz-hint', 'Pick a free provider. Kilo works with no key at all; the others give you a free key in about two minutes.'));
    const mountEl = el('div', 'mz-keywizard');
    box.appendChild(mountEl);

    const skip = el('button', 'mz-linklike', 'Add a provider later');
    skip.type = 'button';
    skip.addEventListener('click', () => { step.status = 'ok'; sfx('step'); renderStepRows(); });
    box.appendChild(skip);
    host.appendChild(box);

    // Preferred: L03's full wizard component, whatever mount shape it ships.
    const w = window.cccFreeKeyWizard || window.CCCFreeKeyWizard;
    if (w) {
      try {
        if (typeof w.mount === 'function') { w.mount(mountEl, { onComplete: () => { step.status = 'ok'; sfx('success'); renderStepRows(); } }); S.wizardMounted = true; return; }
        if (typeof w === 'function') { w(mountEl); S.wizardMounted = true; return; }
        if (typeof w.open === 'function') { w.open(mountEl); S.wizardMounted = true; return; }
      } catch (_) {}
    }

    // Fallback: compact provider cards over /api/free-router/providers.
    mountEl.textContent = '';
    mountEl.appendChild(el('div', 'mz-loading', 'Loading free providers…'));
    let providers = S.providers;
    if (!providers) {
      try { providers = await driver.providers(); } catch (_) { providers = null; }
      S.providers = providers;
    }
    mountEl.textContent = '';
    if (!providers || !Array.isArray(providers) || !providers.length) {
      mountEl.appendChild(el('div', 'mz-hint',
        'Your router will use its built-in free provider. You can add more keys later in Settings.'));
      const ok = el('button', 'mz-btn mz-btn-primary', 'Continue');
      ok.type = 'button';
      ok.addEventListener('click', () => { step.status = 'ok'; renderStepRows(); });
      mountEl.appendChild(ok);
      return;
    }
    const ranked = providers.slice().sort((a, b) => {
      if (!!b.keyless - !!a.keyless) return (!!b.keyless) - (!!a.keyless);
      return (b.coding_score || 0) - (a.coding_score || 0);
    }).slice(0, 3);
    ranked.forEach((p) => mountEl.appendChild(providerCard(p, step)));
  }

  function providerCard(p, step) {
    const card = el('div', 'mz-provider');
    const head = el('div', 'mz-provider-head');
    head.appendChild(el('div', 'mz-provider-name', p.name || p.platform || 'Provider'));
    const badges = el('div', 'mz-provider-badges');
    if (p.keyless) badges.appendChild(el('span', 'mz-badge', 'no key needed'));
    if (p.free_no_card) badges.appendChild(el('span', 'mz-badge', 'free, no card'));
    head.appendChild(badges);
    card.appendChild(head);

    if (p.keyless) {
      if (p.tos_note) card.appendChild(el('div', 'mz-provider-tos', p.tos_note));
      const enable = el('button', 'mz-btn mz-btn-primary', 'I understand, enable it');
      enable.type = 'button';
      enable.addEventListener('click', async () => {
        enable.disabled = true;
        try {
          const r = await driver.submitKey(p.platform);
          if (r && r.ok !== false) {
            sfx('success');
            step.status = 'ok';
            renderStepRows();
            return;
          }
          throw new Error((r && r.error) || 'not enabled');
        } catch (e) {
          enable.disabled = false;
          card.appendChild(el('div', 'mz-error', 'That did not work. You can skip this and try later from Settings.'));
        }
      });
      card.appendChild(enable);
      return card;
    }

    const row = el('div', 'mz-provider-form');
    const input = el('input', 'mz-input');
    input.type = 'password';
    input.placeholder = p.key_hint || 'Paste your free key';
    input.setAttribute('aria-label', 'API key for ' + (p.name || p.platform));
    const validate = el('button', 'mz-btn mz-btn-primary', 'Validate');
    validate.type = 'button';
    const msg = el('div', 'mz-provider-msg');
    validate.addEventListener('click', async () => {
      const key = input.value.trim();
      if (!key) { msg.textContent = 'Paste the key first.'; return; }
      validate.disabled = true;
      msg.textContent = 'Checking…';
      try {
        const r = await driver.submitKey(p.platform, key);
        if (r && (r.ok || r.validated)) {
          sfx('success');
          msg.textContent = 'Key works. Saved locally.';
          step.status = 'ok';
          setTimeout(renderStepRows, 500);
          return;
        }
        msg.textContent = (r && r.error) || 'That key did not validate. Check it and try again.';
      } catch (e) {
        msg.textContent = 'Could not reach the router to validate. Try again in a moment.';
      }
      validate.disabled = false;
    });
    row.appendChild(input);
    row.appendChild(validate);
    card.appendChild(row);
    card.appendChild(msg);
    if (p.signup_url) {
      const a = el('a', 'mz-linklike mz-signup', 'Get a free key at ' + String(p.signup_url).replace(/^https?:\/\//, '').replace(/\/.*$/, ''));
      a.href = p.signup_url;
      a.target = '_blank';
      a.rel = 'noopener';
      card.appendChild(a);
    }
    if (p.tos_note) card.appendChild(el('div', 'mz-provider-tos', p.tos_note));
    return card;
  }

  // ── first_task scene fragment ──
  function renderFirstTaskStep(host, step) {
    host.textContent = '';
    const box = el('div', 'mz-taskbox');
    box.appendChild(el('div', 'mz-hint', 'Pick one. Your agent does it alone, start to finish, on a $0 model.'));
    const grid = el('div', 'mz-task-grid');
    FIRST_TASKS.forEach((t) => {
      const c = el('button', 'mz-task-card');
      c.type = 'button';
      c.appendChild(el('div', 'mz-task-title', t.title));
      c.appendChild(el('div', 'mz-task-desc', t.desc));
      c.addEventListener('click', () => startFirstTask(t, step, box));
      grid.appendChild(c);
    });
    box.appendChild(grid);
    const skip = el('button', 'mz-linklike', 'Skip to the finale');
    skip.type = 'button';
    skip.addEventListener('click', () => { step.status = 'ok'; goFinale(); });
    box.appendChild(skip);
    host.appendChild(box);
    const saved = lsGet(LS_FIRST_TASK);
    if (saved) startFirstTask(null, step, box, saved);
  }

  function firstTaskError(box, step, message) {
    box.textContent = '';
    const error = el('div', 'mz-task-error');
    error.setAttribute('role', 'alert');
    error.appendChild(el('p', 'mz-hint', message));
    const retry = el('button', 'mz-btn mz-btn-primary', 'Try again');
    retry.type = 'button';
    retry.addEventListener('click', () => renderFirstTaskStep(box.parentNode, step));
    error.appendChild(retry);
    const setup = el('a', 'mz-linklike', 'Set up free models');
    setup.href = '/free-router';
    error.appendChild(setup);
    box.appendChild(error);
  }

  async function startFirstTask(task, step, box, savedJob) {
    box.textContent = '';
    const run = el('div', 'mz-taskrun');
    run.appendChild(el('div', 'mz-taskrun-title', 'Your agent is on it.'));
    const status = el('div', 'mz-taskrun-status', 'Spinning up a $0 model…');
    status.setAttribute('aria-live', 'polite');
    run.appendChild(status);
    const bar = el('div', 'mz-progress mz-progress-indet');
    bar.appendChild(el('span', 'mz-progress-fill'));
    run.appendChild(bar);
    box.appendChild(run);
    sfx('step');

    let res = null;
    try {
      res = savedJob ? { job_id: savedJob } : await driver.firstTask(task.id);
    } catch (e) {
      // An unavailable first-task runner remains retryable, without a finale.
      firstTaskError(box, step, (e && e.message) || 'The task could not start. Try again.');
      return;
    }

    // The first-task endpoint returns a job; its own poll carries the result
    // and run-specific usage, rather than the installer's shared registry.
    if (res && typeof res.saved_usd === 'number') S.savedUsd = res.saved_usd;
    if (res && typeof res.api_value_usd === 'number' && S.savedUsd == null) S.savedUsd = res.api_value_usd;

    if (res && res.job_id) {
      S.firstTaskJobId = res.job_id;
      lsSet(LS_FIRST_TASK, res.job_id);
      status.textContent = 'Working. Watching it think.';
      const finish = async () => {
        if (!S.open || !box.isConnected) return;
        try {
          const j = await driver.pollFirstTask(res.job_id);
          if (!j) throw new Error('The task is no longer available. Try again.');
          const lines = Array.isArray(j.lines) ? j.lines : [];
          const tail = lines.slice(-1)[0];
          if (tail) status.textContent = String(tail.text || tail).slice(0, 160);
          if (j.status !== 'running') {
            S.firstTaskJobId = null;
            lsDel(LS_FIRST_TASK);
            if (j.status === 'done' && j.runtime === 'free' && j.result && j.result.verified) {
              S.firstTaskDone = true;
              S.savedUsd = Number(j.usage && j.usage.api_value_usd) || null;
              step.status = 'ok';
              sfx('success');
              goFinale();
            } else {
              firstTaskError(box, step, j.error || 'The task did not finish on a free model. Try again.');
            }
            return;
          }
        } catch (e) {
          if (e && e.status === 404) { S.firstTaskJobId = null; lsDel(LS_FIRST_TASK); }
          firstTaskError(box, step, (e && e.message) || 'Could not check the task. Try again.');
          return;
        }
        if (S.firstTaskJobId) S.firstTaskTimer = setTimeout(finish, POLL_MS);
      };
      clearTimeout(S.firstTaskTimer);
      finish();
      return;
    }

    firstTaskError(box, step, 'The task did not start. Try again.');
  }

  // ── Scene: Finale ──
  async function goFinale() {
    S.scene = 'finale';
    const card = el('section', 'mz-card mz-finale');
    card.appendChild(el('div', 'mz-eyebrow', 'Moment zero'));
    card.appendChild(el('h2', 'mz-title mz-finale-title', 'You are live.'));
    const big = el('div', 'mz-big-cost', '$0');
    card.appendChild(big);

    const line = el('p', 'mz-finale-line');
    card.appendChild(line);

    const actions = el('div', 'mz-actions');
    const open = el('button', 'mz-btn mz-btn-primary', 'Open my dashboard');
    open.type = 'button';
    open.addEventListener('click', () => { markDone(); close(); });
    actions.appendChild(open);
    const tour = el('button', 'mz-btn mz-btn-ghost', 'Take the 2 minute tour');
    tour.type = 'button';
    tour.addEventListener('click', () => {
      markDone();
      close();
      const t = document.getElementById('takeTourBtn');
      if (t) { try { t.click(); } catch (_) {} }
    });
    actions.appendChild(tour);
    card.appendChild(actions);

    shell(card);
    confetti({ origin: 'center' });
    sfx('success');
    setTimeout(() => sfx('coin'), 600);

    // Savings line: only the verified first task's own usage, never a daily total.
    const usd = S.firstTaskDone ? S.savedUsd : null;
    if (usd != null && isFinite(usd) && usd > 0) {
      line.textContent = '';
      line.appendChild(el('span', null, 'This run cost $0. At API prices it would have cost about '));
      const num = el('strong', 'mz-saved-num');
      line.appendChild(num);
      line.appendChild(document.createTextNode('.'));
      if (reducedMotion()) num.textContent = '$' + Number(usd).toFixed(2);
      else countUp(num, usd);
    } else {
      line.textContent = S.firstTaskDone
        ? 'This run cost $0. Every free-model run from now on does too.'
        : 'Your team is set up and ready. Runs on free models cost $0.';
    }
    focusSoon(open);
  }

  // ── Open/close plumbing ──
  function onKeydown(e) {
    if (!S.open) return;
    if (e.key === 'Escape') {
      e.stopPropagation();
      requestClose();
      return;
    }
    if (e.key === 'Tab') {
      // Tiny focus trap; focus that escaped (or never landed) gets pulled in.
      const focusables = $root.querySelectorAll('button, a[href], input, [tabindex]:not([tabindex="-1"])');
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (!$root.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
      else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }

  function markDone() {
    lsSet(LS_ONBOARDED, String(Date.now()));
    // Moment Zero replaces the engines first-run for new users, so it must
    // never stack on top of us on a later load.
    lsSet(LS_ENG_DONE, String(Date.now()));
    try { window.__cccEnginesFirstRun = false; } catch (_) {}
  }

  function open(opts) {
    opts = opts || {};
    if (S.open) return;
    ensureRoot();
    S.open = true;
    S.force = !!opts.force;
    S.prevFocus = document.activeElement;
    $root.hidden = false;
    // .open is added synchronously: elements inside must be focusable right
    // away (keyboard users land on the primary action without a mouse), and
    // maybeStartFirstFlight defers on the .upd-overlay.open marker anyway.
    $root.classList.add('open');
    document.documentElement.classList.add('mz-no-scroll');
    const scene = opts.scene || (lsGet(LS_JOB) || lsGet(LS_FIRST_TASK) ? 'steps' : 'welcome');
    S.scene = scene;
    S.firstTaskDone = false;
    S.savedUsd = null;
    if (scene === 'steps') { renderSteps(); loadPlan(); }
    else if (scene === 'finale') { goFinale(); }
    else renderWelcome();
  }

  function requestClose() {
    // A running installer keeps going server-side; closing only hides us.
    if (S.jobId) {
      const live = document.getElementById('mzLive');
      if (live) live.textContent = 'Setup keeps running in the background.';
      setTimeout(() => { markDone(); close(); }, 350);
      return;
    }
    markDone();
    close();
  }

  function close() {
    if (!S.open) return;
    S.open = false;
    clearTimeout(S.jobTimer);
    S.jobTimer = null;
    clearTimeout(S.firstTaskTimer);
    S.firstTaskTimer = null;
    $root.classList.remove('open');
    $root.hidden = true;
    document.documentElement.classList.remove('mz-no-scroll');
    // A completion ping only makes sense from the finale — skipping the
    // welcome is not a "setup complete" event worth notifying about.
    if (S.scene === 'finale') {
      try {
        if (window.cccNotify) window.cccNotify('Setup complete', 'Your team is ready. Runs on free models cost $0.', 'success');
      } catch (_) {}
    }
    if (S.prevFocus && typeof S.prevFocus.focus === 'function') {
      try { S.prevFocus.focus({ preventScroll: true }); } catch (_) {}
    }
    S.prevFocus = null;
  }

  // ── Auto-open + the first-run handoff with app.js ──
  function forcedByUrl() {
    try {
      return new URLSearchParams(window.location.search || '').get('onboarding') === '1';
    } catch (_) { return false; }
  }

  function autoEligible() {
    if (isPopout) return false;
    // The auto-open is a pop-up (static/popups.js); /?onboarding=1 is not.
    if (!(window.cccPopups && window.cccPopups.allowed('moment-zero'))) return false;
    if (lsGet(LS_ONBOARDED)) return false;
    if (lsGet(LS_ENG_DONE) || lsGet('ccc-tour-done')) return false;
    return true;
  }

  // app.js calls this before showing the engines first-run. Resolves true
  // when Moment Zero takes over the first-run slot.
  function claimFirstRun() {
    if (forcedByUrl()) {
      markDone();
      open();
      return Promise.resolve(true);
    }
    if (!autoEligible()) return Promise.resolve(false);
    return driver.freshInstall()
      .then((d) => {
        if (d && d.fresh_install) {
          markDone();
          open();
          return true;
        }
        return false;
      })
      .catch(() => false);
  }

  function maybeAutoOpen() {
    if (forcedByUrl()) { open(); return; }
    if (!autoEligible()) return;
    driver.freshInstall()
      .then((d) => { if (d && d.fresh_install) open(); })
      .catch(() => {});
  }

  // Settings row click (delegated so index.html stays a one-line edit).
  document.addEventListener('click', (e) => {
    const t = e.target && e.target.closest ? e.target.closest('#momentZeroBtn') : null;
    if (!t) return;
    e.preventDefault();
    try {
      if (typeof window._cccCloseSettingsModal === 'function') window._cccCloseSettingsModal();
    } catch (_) {}
    open({ force: true });
  });

  // Auto-open on a fresh install once the DOM is up. claimFirstRun() remains
  // the authority app.js waits on — this path covers servers old enough to
  // lack the endpoint (claim resolves false and the engines screen runs).
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', maybeAutoOpen, { once: true });
  } else {
    setTimeout(maybeAutoOpen, 0);
  }

  window.cccOnboarding = {
    open,
    close,
    isOpen: () => S.open,
    claimFirstRun,
    shouldAutoOpen: autoEligible,
    _setDriver: (d) => { driver = d || realDriver; },
    _state: S,
  };
})();
