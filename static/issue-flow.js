/*
 * GitHub issue auto-fix wizard.
 *
 * Walks someone from nothing to a working flow:
 *
 *   their product -> GitHub issue -> WatchTower queue -> agent -> issue closed
 *
 * Five steps: how it works (plus readiness checks), pick the repo, create the
 * queue, connect the product, run one real test issue and watch it travel.
 * The finish hands over to the Queues board, where the flow lives from then on.
 *
 * Backend: ccc_server/issue_flow.py (/api/issue-flow/*). Queue creation goes
 * through the same /api/queue/config and /api/queue/drain the Queues board
 * uses, so a queue made here is an ordinary queue.
 *
 * Public API:
 *   window.cccIssueFlow.open({ queue })   open the wizard (queue: jump to its test)
 *   window.cccIssueFlow.close()
 *   window.cccIssueFlow.isOpen()
 *   window.cccIssueFlow.spotlight(queue)  point at a queue row on the Queues board
 *
 * Entry points: any element with [data-issue-flow-open], and /?issue-flow=1.
 * Loaded by index.html and q2.html; shares no code with either.
 */
(function () {
  'use strict';
  if (window.cccIssueFlow) return;

  const API = '/api/issue-flow';
  const LS_KEY = 'ccc-issue-flow-v1';
  const POLL_MS = 3000;
  const STEPS = [
    { id: 'intro', name: 'How it works' },
    { id: 'repo', name: 'Pick the repo' },
    { id: 'queue', name: 'Create the queue' },
    { id: 'connect', name: 'Connect your product' },
    { id: 'test', name: 'Run a test' },
  ];

  const I = {
    bolt: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M13.4 2.5 4.6 13.3a.6.6 0 0 0 .5 1h5.3l-1 6.9a.4.4 0 0 0 .7.3l8.8-10.8a.6.6 0 0 0-.5-1h-5.3l1-6.9a.4.4 0 0 0-.7-.3Z"/></svg>',
    product: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="12.5" rx="2.2"/><path d="M8.5 20.5h7M12 16.5v4M7.5 9l2.2 2-2.2 2M12.5 13h3.5"/></svg>',
    issue: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" aria-hidden="true"><circle cx="12" cy="12" r="8.6"/><circle cx="12" cy="12" r="2.3" fill="currentColor" stroke="none"/></svg>',
    queue: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="4.5" width="16" height="4" rx="1.3"/><rect x="4" y="10" width="16" height="4" rx="1.3"/><rect x="4" y="15.5" width="16" height="4" rx="1.3"/></svg>',
    agent: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M11 3.5l1.9 5.2 5.2 1.9-5.2 1.9L11 17.7l-1.9-5.2-5.2-1.9 5.2-1.9z"/><path d="M18.3 15.2l.8 2.1 2.1.8-2.1.8-.8 2.1-.8-2.1-2.1-.8 2.1-.8z"/></svg>',
    closed: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="8.6"/><path d="M8.2 12.4l2.6 2.6 5-5.4"/></svg>',
    tick: '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 6.4l2.3 2.3 4.7-5"/></svg>',
    cross: '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M3 3l6 6M9 3l-6 6"/></svg>',
    warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.5 2.6 19.5h18.8L12 3.5Z"/><path d="M12 10v4.5M12 17.2v.3"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="8.8"/><path d="M12 11v5.2M12 7.6v.3"/></svg>',
    dot: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="12" cy="12" r="4"/></svg>',
    arrow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="10.5" width="14" height="9.5" rx="2"/><path d="M8.2 10.5V7.8a3.8 3.8 0 0 1 7.6 0v2.7"/></svg>',
  };

  const NODES = [
    { id: 'product', label: 'Your product', icon: I.product },
    { id: 'issue', label: 'GitHub issue', icon: I.issue },
    { id: 'queue', label: 'Queue', icon: I.queue },
    { id: 'agent', label: 'Agent', icon: I.agent },
    { id: 'closed', label: 'Issue closed', icon: I.closed },
  ];

  // ── State ──────────────────────────────────────────────────────────
  const S = fresh();
  let $scrim = null;
  let pollTimer = null;
  let tickTimer = null;
  let lastFocus = null;

  function fresh() {
    return {
      step: 'intro',
      status: null, statusErr: '', statusLoading: false,
      filter: '', manualOpen: false,
      manual: { github_repo: '', repo_path: '' },
      sel: null,                 // { github_repo, repo_path }
      info: null, infoErr: '', infoLoading: false,
      form: { queue: '', mode: 'labeled', hold: true, workers: 1, ack: false, touchedMode: false },
      creating: null,            // { steps: [{id,label,state,error}], busy }
      flow: null,                // { queue, github_repo, repo_path, mode, grace_s, private }
      tab: 'curl',
      test: null,                // { number, url, ref, startedAt, data, times, error, sending }
      finished: false,
    };
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  async function api(path, opts) {
    const res = await fetch(path, opts);
    let data = {};
    try { data = await res.json(); } catch (_) { /* non-JSON error page */ }
    if (!res.ok || data.ok === false) throw new Error(data.error || ('HTTP ' + res.status));
    return data;
  }
  function post(path, body) {
    return api(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
  }

  function reducedMotion() {
    try {
      if (window.cccFx && typeof window.cccFx.reducedMotion === 'function') return !!window.cccFx.reducedMotion();
    } catch (_) {}
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }
  function celebrate() {
    try {
      if (reducedMotion() || !window.cccFx) return;
      if (typeof window.cccFx.confetti === 'function') window.cccFx.confetti();
      if (typeof window.cccFx.play === 'function') window.cccFx.play('success');
    } catch (_) { /* a bonus, never a blocker */ }
  }

  function loadCss() {
    if (document.querySelector('link[href^="/static/issue-flow.css"]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/static/issue-flow.css';
    document.head.appendChild(link);
  }

  // An unfinished run survives closing the wizard (and a page reload), so a
  // test issue that is still travelling is not orphaned.
  function persist() {
    try {
      if (!S.flow || S.finished) { localStorage.removeItem(LS_KEY); return; }
      localStorage.setItem(LS_KEY, JSON.stringify({
        flow: S.flow,
        step: S.step,
        test: S.test && S.test.number ? {
          number: S.test.number, url: S.test.url, ref: S.test.ref,
          startedAt: S.test.startedAt, times: S.test.times,
        } : null,
      }));
    } catch (_) {}
  }
  function restore() {
    try {
      const saved = JSON.parse(localStorage.getItem(LS_KEY) || 'null');
      return saved && saved.flow && saved.flow.queue ? saved : null;
    } catch (_) { return null; }
  }

  function runLabel() { return (S.status && S.status.labels && S.status.labels.run) || 'watchtower:play'; }
  function holdLabel() { return (S.status && S.status.labels && S.status.labels.hold) || 'watchtower:no-auto-drain'; }
  function modeOf(flow) { return flow && flow.mode === 'all' ? 'all' : 'labeled'; }
  function fmtClock(seconds) {
    const s = Math.max(0, Math.round(seconds));
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }
  function fmtGrace(seconds) {
    if (!seconds) return 'right away';
    if (seconds < 60) return 'after ' + seconds + ' seconds';
    const m = Math.round(seconds / 60);
    return 'after ' + m + (m === 1 ? ' minute' : ' minutes');
  }

  // ── Shell ──────────────────────────────────────────────────────────
  function mount() {
    loadCss();
    $scrim = document.createElement('div');
    $scrim.className = 'iflow-scrim';
    $scrim.innerHTML =
      '<div class="iflow" role="dialog" aria-modal="true" aria-labelledby="iflowTitle">'
      + '<header class="iflow-head">'
      + '<span class="iflow-bolt">' + I.bolt + '</span>'
      + '<div class="iflow-head-text"><h1 id="iflowTitle">Auto-fix issues from production</h1>'
      + '<p>Your product opens a GitHub issue. An agent picks it up, fixes it and closes it.</p></div>'
      + '<button type="button" class="iflow-x" data-if="close" aria-label="Close">&times;</button>'
      + '</header>'
      + '<div class="iflow-pipe" data-if-pipe aria-hidden="true"></div>'
      + '<div class="iflow-body"><ol class="iflow-steps" data-if-steps></ol>'
      + '<section class="iflow-pane" data-if-pane tabindex="-1"></section></div>'
      + '<footer class="iflow-foot" data-if-foot></footer>'
      + '</div>';
    $scrim.addEventListener('click', onClick);
    $scrim.addEventListener('input', onInput);
    $scrim.addEventListener('change', onInput);
    $scrim.addEventListener('keydown', onKey);
    $scrim.addEventListener('mousedown', (ev) => { if (ev.target === $scrim) close(); });
    document.body.appendChild($scrim);
  }

  function q(sel) { return $scrim ? $scrim.querySelector(sel) : null; }

  function render() {
    if (!$scrim) return;
    renderPipe();
    renderSteps();
    const pane = q('[data-if-pane]');
    pane.innerHTML = paneHtml();
    pane.scrollTop = 0;
    renderFoot();
  }

  // ── Pipeline diagram ───────────────────────────────────────────────
  function testStage() {
    return S.test && S.test.data ? String(S.test.data.stage || 'sent') : (S.test && S.test.number ? 'sent' : '');
  }

  function pipeModel() {
    const flow = S.flow;
    const repo = (flow && flow.github_repo) || (S.info && S.info.github_repo) || (S.sel && S.sel.github_repo) || '';
    const queue = (flow && flow.queue) || '';
    const workers = flow && flow.workers ? flow.workers : (S.step === 'queue' ? S.form.workers : 0);
    const caps = {
      product: '',
      issue: repo,
      queue: queue || (S.step === 'queue' ? String(S.form.queue || '').toUpperCase() : ''),
      agent: workers ? workers + (workers === 1 ? ' worker' : ' workers') : '',
      closed: '',
    };
    const st = { product: '', issue: '', queue: '', agent: '', closed: '' };
    const links = ['', '', '', ''];
    let demo = false;
    if (S.finished) {
      NODES.forEach((n) => { st[n.id] = 'done'; });
      links.fill('done');
    } else if (S.step === 'intro') {
      demo = true;
    } else if (S.step === 'repo') {
      st.issue = 'focus';
    } else if (S.step === 'queue') {
      st.issue = 'done'; st.queue = 'focus'; st.agent = 'focus'; links[1] = 'done';
    } else if (S.step === 'connect') {
      st.product = 'focus'; st.issue = 'done'; st.queue = 'done'; st.agent = 'done';
      links[1] = 'done'; links[2] = 'done';
    } else if (S.step === 'test') {
      const stage = testStage();
      if (!stage) {
        st.product = 'focus'; st.issue = 'done'; st.queue = 'done'; st.agent = 'done';
        links[1] = 'done'; links[2] = 'done';
      } else if (stage === 'sent') {
        st.product = 'done'; st.issue = 'live'; links[0] = 'done'; links[1] = 'flow';
      } else if (stage === 'grace' || stage === 'queued' || stage === 'parked') {
        st.product = 'done'; st.issue = 'done'; st.queue = 'live';
        links[0] = 'done'; links[1] = 'done'; links[2] = stage === 'parked' ? '' : 'flow';
      } else if (stage === 'working' || stage === 'blocked') {
        st.product = 'done'; st.issue = 'done'; st.queue = 'done'; st.agent = 'live';
        links[0] = 'done'; links[1] = 'done'; links[2] = 'done'; links[3] = stage === 'working' ? 'flow' : '';
      } else if (stage === 'done') {
        NODES.forEach((n) => { st[n.id] = 'done'; });
        links.fill('done');
      }
      if (S.test && S.test.number) caps.issue = '#' + S.test.number;
    }
    return { caps, st, links, demo };
  }

  function renderPipe() {
    const el = q('[data-if-pipe]');
    if (!el) return;
    const m = pipeModel();
    el.classList.toggle('is-demo', m.demo);
    let html = '';
    NODES.forEach((n, i) => {
      if (i) html += '<span class="iflow-link' + (m.links[i - 1] ? ' is-' + m.links[i - 1] : '') + '" style="--i:' + (i - 1) + '"></span>';
      html += '<span class="iflow-node' + (m.st[n.id] ? ' is-' + m.st[n.id] : '') + '">'
        + '<span class="iflow-node-dot" style="--i:' + i + '">' + n.icon
        + '<span class="iflow-node-tick">' + I.tick + '</span></span>'
        + '<span class="iflow-node-label">' + esc(n.label) + '</span>'
        + '<span class="iflow-node-cap" title="' + esc(m.caps[n.id]) + '">' + esc(m.caps[n.id]) + '</span>'
        + '</span>';
    });
    el.innerHTML = html;
  }

  // ── Steps rail ─────────────────────────────────────────────────────
  function stepIndex(id) { return STEPS.findIndex((s) => s.id === id); }

  // A finished step can be revisited, but once the queue exists the two
  // steps that built it are settled: going back there would only offer to
  // build it again.
  function canVisit(id) {
    if (id === S.step) return false;
    const target = stepIndex(id);
    if (target > stepIndex(S.step) && !S.finished) return false;
    if (S.flow) return id === 'connect' || id === 'test';
    return target < stepIndex(S.step);
  }

  function stepSub(id) {
    if (id === 'repo') return (S.flow && S.flow.github_repo) || (S.info && S.info.github_repo) || '';
    if (id === 'queue') return (S.flow && S.flow.queue) || '';
    if (id === 'test' && S.test && S.test.number) return '#' + S.test.number;
    return '';
  }

  function renderSteps() {
    const el = q('[data-if-steps]');
    if (!el) return;
    const cur = stepIndex(S.step);
    el.innerHTML = STEPS.map((s, i) => {
      const done = S.finished || i < cur;
      const current = !S.finished && i === cur;
      const cls = 'iflow-step' + (done ? ' is-done' : '') + (current ? ' is-current' : '');
      const visit = canVisit(s.id);
      const sub = stepSub(s.id);
      return '<li><button type="button" class="' + cls + '"'
        + (visit ? ' data-if="goto" data-step="' + s.id + '"' : ' tabindex="-1"')
        + (current ? ' aria-current="step"' : '') + '>'
        + '<span class="iflow-step-num">' + (done ? I.tick : (i + 1)) + '</span>'
        + '<span><span class="iflow-step-name">' + esc(s.name) + '</span>'
        + (sub ? '<span class="iflow-step-sub" title="' + esc(sub) + '">' + esc(sub) + '</span>' : '')
        + '</span></button></li>';
    }).join('');
  }

  // ── Footer ─────────────────────────────────────────────────────────
  function footModel() {
    const f = { back: null, note: '', bad: false, primary: null };
    if (S.finished) {
      f.back = { label: 'Set up another repo', act: 'restart' };
      f.note = '';
      f.primary = { label: 'Close', act: 'close', cls: '' };
      return f;
    }
    if (S.step === 'intro') {
      const ready = S.status && S.status.ready;
      f.note = S.statusLoading ? 'Checking this machine…'
        : (S.status ? (ready ? 'Takes about three minutes.' : 'Fix the item above, then check again.') : '');
      f.primary = { label: 'Start', act: 'next', disabled: !ready, icon: true };
    } else if (S.step === 'repo') {
      const v = repoVerdict();
      f.back = { label: 'Back', act: 'back' };
      f.note = v.note; f.bad = v.bad;
      f.primary = { label: 'Next', act: 'next', disabled: !v.ok, icon: true };
    } else if (S.step === 'queue') {
      const v = queueVerdict();
      const busy = S.creating && S.creating.busy;
      const failed = S.creating && S.creating.steps.some((s) => s.state === 'err');
      f.back = busy ? null : { label: 'Back', act: 'back' };
      f.note = v.note; f.bad = v.bad;
      f.primary = { label: busy ? 'Creating…' : (failed ? 'Try again' : 'Create queue'),
                    act: 'create', disabled: busy || !v.ok };
    } else if (S.step === 'connect') {
      f.note = 'You can wire your product up later. The test works either way.';
      f.primary = { label: 'Next: run a test', act: 'next', icon: true };
    } else if (S.step === 'test') {
      f.back = { label: 'Back', act: 'back' };
      const stage = testStage();
      if (stage === 'done') {
        f.primary = { label: 'Finish', act: 'finish', cls: 'is-go', icon: true };
      } else if (stage) {
        f.note = 'You can close this window. The test keeps running and this picks up where it left off.';
        f.primary = { label: 'Finish without waiting', act: 'finish', cls: '' };
      } else {
        f.primary = { label: 'Skip the test', act: 'finish', cls: '' };
      }
    }
    return f;
  }

  function renderFoot() {
    const el = q('[data-if-foot]');
    if (!el) return;
    const f = footModel();
    const p = f.primary;
    el.innerHTML =
      (f.back ? '<button type="button" class="iflow-btn is-ghost" data-if="' + f.back.act + '">' + esc(f.back.label) + '</button>' : '')
      + '<span class="iflow-foot-note' + (f.bad ? ' is-bad' : '') + '" role="status">' + esc(f.note || '') + '</span>'
      + (p ? '<button type="button" class="iflow-btn ' + (p.cls === undefined ? 'is-primary' : p.cls) + '" data-if="' + p.act + '"'
          + (p.disabled ? ' disabled' : '') + '>' + esc(p.label) + (p.icon ? I.arrow : '') + '</button>' : '');
  }

  // ── Panes ──────────────────────────────────────────────────────────
  function paneHtml() {
    if (S.finished) return finishHtml();
    if (S.step === 'intro') return introHtml();
    if (S.step === 'repo') return repoHtml();
    if (S.step === 'queue') return queueHtml();
    if (S.step === 'connect') return connectHtml();
    return testHtml();
  }

  function stateDot(kind) {
    const icon = kind === 'ok' ? I.tick : kind === 'bad' ? I.cross : kind === 'warn' ? '!' : '';
    return '<span class="iflow-state' + (kind ? ' is-' + kind : '') + '">' + icon + '</span>';
  }
  function callout(kind, html, actions) {
    return '<div class="iflow-callout is-' + kind + '">' + (kind === 'info' ? I.info : I.warn)
      + '<div class="iflow-callout-body">' + html
      + (actions ? '<div class="iflow-callout-actions">' + actions + '</div>' : '')
      + '</div></div>';
  }
  function cmd(text) {
    return '<span class="iflow-cmd">' + esc(text)
      + '<button type="button" class="iflow-btn is-small" data-if="copy" data-copy="' + esc(text) + '">Copy</button></span>';
  }

  // Step 1 ─ how it works + readiness
  function introHtml() {
    const st = S.status;
    let checks;
    if (!st) {
      checks = S.statusErr
        ? '<div class="iflow-row">' + stateDot('bad') + '<div class="iflow-row-main"><div class="iflow-row-title">Could not check this machine</div>'
          + '<div class="iflow-row-sub">' + esc(S.statusErr) + '</div></div>'
          + '<button type="button" class="iflow-btn is-small" data-if="recheck">Try again</button></div>'
        : '<div class="iflow-row">' + stateDot('run') + '<div class="iflow-row-main"><div class="iflow-row-title">Checking this machine…</div></div></div>';
    } else {
      const gh = st.gh || {};
      const wt = st.watchtower || {};
      const ghRow = gh.authed
        ? row('ok', 'GitHub CLI is signed in', 'As ' + esc(gh.login) + '. This is the account that reads and closes issues.')
        : gh.installed
          ? row('bad', 'GitHub CLI is not signed in', 'Run this in a terminal on this machine, then check again.<br>' + cmd('gh auth login'))
          : row('bad', 'GitHub CLI is not installed', 'Install <a href="https://cli.github.com" target="_blank" rel="noopener">gh</a>, then run '
              + '<code>gh auth login</code> and check again.');
      const wtRow = wt.running
        ? row('ok', 'WatchTower is running', 'It watches the queue and starts the agents.')
        : wt.installed
          ? row('bad', 'WatchTower is not running', 'Start it, then check again.<br>' + cmd('wt start'))
          : row('bad', 'WatchTower is not installed', 'It ships with CCC. Re-run the CCC installer, then check again.');
      checks = ghRow + wtRow;
      if (!st.ready) {
        checks += '<div class="iflow-row"><div class="iflow-row-main"></div>'
          + '<button type="button" class="iflow-btn is-small" data-if="recheck"' + (S.statusLoading ? ' disabled' : '') + '>'
          + (S.statusLoading ? 'Checking…' : 'Check again') + '</button></div>';
      }
    }
    const allFlows = (st && st.flows) || [];
    const flows = allFlows.slice(0, 4);
    const more = allFlows.length - flows.length;
    const existing = flows.length ? '<h3 class="iflow-h3">Already set up on this machine</h3><div class="iflow-card">'
      + flows.map((f) => '<div class="iflow-row">' + stateDot('ok')
          + '<div class="iflow-row-main"><div class="iflow-row-title">' + esc(f.queue) + '</div>'
          + '<div class="iflow-row-sub">' + esc(f.github_repo) + ' &middot; '
          + (f.mode === 'all' ? 'every issue' : 'tagged issues only') + '</div></div>'
          + '<button type="button" class="iflow-btn is-small" data-if="use-flow" data-queue="' + esc(f.queue) + '" data-step="test">Test</button>'
          + '<button type="button" class="iflow-btn is-small" data-if="open-existing" data-queue="' + esc(f.queue) + '">Open</button>'
          + '</div>').join('')
      + (more > 0 ? '<div class="iflow-row"><div class="iflow-row-main"><div class="iflow-row-sub">and ' + more
          + ' more GitHub-backed ' + (more === 1 ? 'queue' : 'queues') + ' in Queues</div></div></div>' : '')
      + '</div>' : '';
    return '<p class="iflow-kicker">Step 1 of 5</p>'
      + '<h2 class="iflow-h2">From a bug in production to a fix, without you in the middle</h2>'
      + '<p class="iflow-lead">This wizard connects three things you already have and proves they work together with one real test.</p>'
      + '<ol class="iflow-how">'
      + '<li><span class="iflow-how-n">1</span><span><b>Your product opens a GitHub issue</b> when something breaks: an exception handler, an alert, a support form.</span></li>'
      + '<li><span class="iflow-how-n">2</span><span><b>A WatchTower queue watches that repo</b> and picks the issue up as a ticket.</span></li>'
      + '<li><span class="iflow-how-n">3</span><span><b>An agent works on it</b> in your local checkout, then closes the issue with a summary of what it did.</span></li>'
      + '</ol>'
      + '<h3 class="iflow-h3">Before we start</h3><div class="iflow-card">' + checks + '</div>'
      + existing;
  }
  function row(kind, title, subHtml) {
    return '<div class="iflow-row">' + stateDot(kind) + '<div class="iflow-row-main">'
      + '<div class="iflow-row-title">' + esc(title) + '</div>'
      + '<div class="iflow-row-sub">' + subHtml + '</div></div></div>';
  }

  // Step 2 ─ repo
  function flowForRepo(slug) {
    const want = String(slug || '').toLowerCase();
    return ((S.status && S.status.flows) || []).find((f) => String(f.github_repo).toLowerCase() === want) || null;
  }

  function repoVerdict() {
    if (S.infoLoading) return { ok: false, note: 'Reading the repo from GitHub…' };
    if (S.infoErr) return { ok: false, bad: true, note: S.infoErr };
    const info = S.info;
    if (!info) return { ok: false, note: 'Pick the repo your product will file issues in.' };
    if (info.existing_queues && info.existing_queues.length) {
      return { ok: false, note: 'This repo already feeds ' + info.existing_queues[0] + '.' };
    }
    if (!info.issues_enabled) return { ok: false, bad: true, note: 'Issues are turned off on this repo.' };
    if (!info.can_write) return { ok: false, bad: true, note: 'Your GitHub login cannot write to this repo.' };
    const local = info.local || {};
    if (!local.exists || !local.is_git) return { ok: false, note: 'Agents need a local checkout of this repo.' };
    return { ok: true, note: info.github_repo + ' is ready.' };
  }

  function repoHtml() {
    const repos = (S.status && S.status.repos) || [];
    const filter = S.filter.trim().toLowerCase();
    const shown = repos.filter((r) => !filter
      || r.github_repo.toLowerCase().includes(filter) || r.name.toLowerCase().includes(filter));
    const selSlug = S.sel ? String(S.sel.github_repo).toLowerCase() : '';
    const selPath = S.sel ? S.sel.repo_path : '';
    const grid = shown.length
      ? '<div class="iflow-repo-grid">' + shown.map((r) => {
          const on = r.github_repo.toLowerCase() === selSlug && r.path === selPath;
          const used = flowForRepo(r.github_repo);
          return '<button type="button" class="iflow-repo' + (on ? ' is-on' : '') + '" data-if="pick-repo"'
            + ' data-repo="' + esc(r.github_repo) + '" data-path="' + esc(r.path) + '" title="' + esc(r.path) + '">'
            + '<span class="iflow-repo-name"><span>' + esc(r.name) + '</span>'
            + (used ? '<span class="iflow-chip is-accent">' + esc(used.queue) + '</span>' : '') + '</span>'
            + '<span class="iflow-repo-slug">' + esc(r.github_repo) + '</span></button>';
        }).join('') + '</div>'
      : '<div class="iflow-card iflow-empty">' + (repos.length
          ? 'No repo matches that filter.'
          : 'No local checkouts with a GitHub remote were found. Enter the repo below.') + '</div>';
    const manual = S.manualOpen || !repos.length;
    return '<p class="iflow-kicker">Step 2 of 5</p>'
      + '<h2 class="iflow-h2">Which repo should your product file issues in?</h2>'
      + '<p class="iflow-lead">Pick the GitHub repo that holds the code. The queue watches its issues, and agents work in your local checkout of it.</p>'
      + (repos.length > 8 ? '<label class="iflow-field"><input class="iflow-input" data-if-in="filter" placeholder="Filter repos" value="'
          + esc(S.filter) + '" aria-label="Filter repos"></label>' : '')
      + '<div data-if-repos>' + grid + '</div>'
      + (manual
        ? '<h3 class="iflow-h3">Enter it yourself</h3><div class="iflow-two">'
          + '<label class="iflow-field"><span class="iflow-field-label">GitHub repo</span>'
          + '<input class="iflow-input" data-if-in="manual-repo" placeholder="owner/repo" value="' + esc(S.manual.github_repo) + '"></label>'
          + '<label class="iflow-field"><span class="iflow-field-label">Local checkout</span>'
          + '<input class="iflow-input" data-if-in="manual-path" placeholder="/path/to/your/checkout" value="' + esc(S.manual.repo_path) + '"></label>'
          + '</div><button type="button" class="iflow-btn is-small" data-if="check-manual">Check this repo</button>'
        : '<p style="margin:12px 0 0"><button type="button" class="iflow-linklike" data-if="manual-open">My repo is not listed</button></p>')
      + '<div data-if-info>' + repoInfoHtml() + '</div>';
  }

  function repoInfoHtml() {
    if (S.infoLoading) {
      return '<h3 class="iflow-h3">Checking</h3><div class="iflow-card"><div class="iflow-row">' + stateDot('run')
        + '<div class="iflow-row-main"><div class="iflow-row-title">Reading ' + esc((S.sel && S.sel.github_repo) || 'the repo') + ' from GitHub…</div></div></div></div>';
    }
    if (S.infoErr) return callout('bad', esc(S.infoErr));
    const info = S.info;
    if (!info) return '';
    const local = info.local || {};
    let html = '<h3 class="iflow-h3">' + esc(info.github_repo) + '</h3><div class="iflow-facts">'
      + '<span class="iflow-chip ' + (info.private ? 'is-ok' : 'is-warn') + '">' + (info.private ? 'Private' : 'Public') + '</span>'
      + '<span class="iflow-chip">' + info.open_issues + (info.open_issues === 1 ? ' open issue' : ' open issues') + '</span>'
      + '<span class="iflow-chip ' + (info.can_write ? 'is-ok' : 'is-bad') + '">' + (info.can_write ? 'Write access' : 'No write access') + '</span>'
      + (local.exists && local.is_git ? '<span class="iflow-chip is-ok" title="' + esc(local.path) + '">Local checkout found</span>' : '')
      + '</div>';
    if (info.existing_queues && info.existing_queues.length) {
      const name = info.existing_queues[0];
      return html + callout('info', '<b>' + esc(name) + '</b> already watches this repo, so there is nothing to create. '
        + 'Connect your product to it or run a test.',
        '<button type="button" class="iflow-btn is-small is-primary" data-if="use-flow" data-queue="' + esc(name) + '" data-step="connect">Use ' + esc(name) + '</button>'
        + '<button type="button" class="iflow-btn is-small" data-if="open-existing" data-queue="' + esc(name) + '">Open it in Queues</button>');
    }
    if (!info.issues_enabled) {
      html += callout('bad', 'Issues are turned off on this repo. Turn them on under the repo&rsquo;s Settings on GitHub, then pick it again.');
    } else if (!info.can_write) {
      html += callout('bad', 'Your GitHub login has <b>' + esc(String(info.permission || 'no').toLowerCase())
        + '</b> access here. The queue needs write access to label and close issues.');
    }
    if (!local.path || !local.exists || !local.is_git) {
      html += callout('warn', (local.path && !local.exists
          ? '<b>' + esc(local.path) + '</b> does not exist on this machine. '
          : local.path ? '<b>' + esc(local.path) + '</b> is not a git checkout. ' : '')
        + 'Agents work in a local checkout. Clone the repo, then enter where it lives.'
        + '<label class="iflow-field" style="margin:9px 0 0"><input class="iflow-input" data-if-in="path-fix" placeholder="/path/to/your/checkout" value="'
        + esc((S.sel && S.sel.repo_path) || '') + '" aria-label="Local checkout path"></label>',
        '<button type="button" class="iflow-btn is-small" data-if="check-path">Check this path</button>');
    } else if (local.origin && !local.matches) {
      html += callout('warn', 'The checkout at <b>' + esc(local.path) + '</b> points at <b>' + esc(local.origin)
        + '</b>, not ' + esc(info.github_repo) + '. Agents would work in a different repo than the one the issues are in.');
    } else {
      html += '<p class="iflow-dim" style="margin:9px 0 0;font-size:12.5px">Agents will work in <code>' + esc(local.path) + '</code></p>';
    }
    return html;
  }

  // Step 3 ─ queue
  function queueName() { return String(S.form.queue || '').trim().toUpperCase(); }

  function queueVerdict() {
    const info = S.info;
    if (!info) return { ok: false, note: '' };
    const name = queueName();
    if (!/^[A-Z0-9_-]{1,64}$/.test(name)) return { ok: false, bad: !!name, note: 'Queue names use letters, numbers, - and _.' };
    const mine = S.creating && S.creating.queue === name;
    if (!mine && (info.taken_queues || []).indexOf(name) !== -1) {
      return { ok: false, bad: true, note: 'A queue named ' + name + ' already exists. Pick another name.' };
    }
    if (S.form.mode === 'all' && !info.private && !S.form.ack) {
      return { ok: false, note: 'Tick the box to confirm you want every issue on a public repo worked.' };
    }
    if (S.form.mode === 'all' && info.open_issues > 0 && S.form.hold && info.open_issues > (info.hold_max || 200)) {
      return { ok: false, bad: true, note: 'Too many open issues to hold back one by one. Use tagged issues instead.' };
    }
    return { ok: true, note: '' };
  }

  function queueHtml() {
    const info = S.info;
    if (!info) return '';
    const f = S.form;
    const rec = info.recommended_mode;
    const n = info.open_issues;
    const run = runLabel();
    const hold = holdLabel();
    const recChip = '<span class="iflow-chip is-accent">Recommended</span>';
    const busy = S.creating && S.creating.busy;
    const dis = busy ? ' disabled' : '';

    let allExtra = '';
    if (f.mode === 'all') {
      if (n > 0) {
        allExtra += '<label class="iflow-check"><input type="checkbox" data-if-in="hold"' + (f.hold ? ' checked' : '') + dis + '>'
          + '<span>Leave the ' + n + (n === 1 ? ' issue' : ' issues') + ' already open alone '
          + '<span class="iflow-dim">(adds the <code>' + esc(hold) + '</code> label to ' + (n === 1 ? 'it' : 'them') + ')</span></span></label>';
        if (!f.hold) {
          allExtra += callout('warn', 'Agents will start on all <b>' + n + '</b> open ' + (n === 1 ? 'issue' : 'issues')
            + ' as soon as the queue is created.');
        }
      }
      if (!info.private) {
        allExtra += callout('bad', '<b>This repo is public.</b> Anyone on the internet can open an issue here, and each one '
          + 'becomes instructions for an agent running on this machine.'
          + '<label class="iflow-check" style="margin-top:8px"><input type="checkbox" data-if-in="ack"' + (f.ack ? ' checked' : '') + dis + '>'
          + '<span>I understand, work every issue anyway</span></label>');
      }
    }

    const opt = (mode, title, desc, extra) => '<label class="iflow-opt' + (f.mode === mode ? ' is-on' : '') + '">'
      + '<input type="radio" name="iflowMode" value="' + mode + '" data-if-in="mode"' + (f.mode === mode ? ' checked' : '') + dis + '>'
      + '<span class="iflow-radio"></span><span style="flex:1;min-width:0">'
      + '<span class="iflow-opt-title">' + title + (rec === mode ? recChip : '') + '</span>'
      + '<p class="iflow-opt-desc">' + desc + '</p>'
      + (extra ? '<div class="iflow-opt-extra">' + extra + '</div>' : '') + '</span></label>';

    const progress = S.creating ? '<h3 class="iflow-h3">Setting up</h3><div class="iflow-card">'
      + S.creating.steps.map((s) => '<div class="iflow-row">'
          + stateDot(s.state === 'ok' ? 'ok' : s.state === 'err' ? 'bad' : s.state === 'run' ? 'run' : '')
          + '<div class="iflow-row-main"><div class="iflow-row-title">' + esc(s.label) + '</div>'
          + (s.error ? '<div class="iflow-row-sub" style="color:var(--if-red)">' + esc(s.error) + '</div>' : '')
          + '</div></div>').join('') + '</div>' : '';

    return '<p class="iflow-kicker">Step 3 of 5</p>'
      + '<h2 class="iflow-h2">Create the queue</h2>'
      + '<p class="iflow-lead">A queue is the list of work agents pull from. This one fills itself from the issues on <b>'
      + esc(info.github_repo) + '</b>.</p>'
      + '<label class="iflow-field" style="max-width:340px"><span class="iflow-field-label">Queue name</span>'
      + '<input class="iflow-input" data-if-in="queue" value="' + esc(f.queue) + '" maxlength="64" spellcheck="false" autocapitalize="characters"' + dis + '>'
      + '<span class="iflow-field-hint">Tickets are named after it, like ' + esc(queueName() || 'QUEUE') + '-42 for issue #42.</span></label>'
      + '<h3 class="iflow-h3" style="margin-top:6px">Which issues should agents work on?</h3>'
      + '<div class="iflow-opts">'
      + opt('labeled', 'Only issues my product tags',
          'Your product adds the label <code>' + esc(run) + '</code> when it opens an issue. GitHub lets only people with '
          + 'write access add labels, so nobody else can start an agent. Tagged issues start right away.')
      + opt('all', 'Every new issue in this repo',
          'No label needed. Each new issue starts ' + fmtGrace(180) + ', which leaves time to stop one by hand.', allExtra)
      + '</div>'
      + '<h3 class="iflow-h3">Agents working at once</h3>'
      + '<div class="iflow-inline"><span class="iflow-seg" role="group" aria-label="Agents working at once">'
      + [1, 2, 3].map((w) => '<button type="button" data-if="workers" data-n="' + w + '" class="' + (f.workers === w ? 'is-on' : '') + '"'
          + ' aria-pressed="' + (f.workers === w) + '"' + dis + '>' + w + '</button>').join('')
      + '</span><span class="iflow-dim" style="font-size:12.5px">Start with 1. Each agent uses your model quota while it works.</span></div>'
      + '<div data-if-progress>' + progress + '</div>';
  }

  // Step 4 ─ connect
  function snippet() {
    const flow = S.flow || {};
    const labels = Array.isArray(flow.labels) ? flow.labels.slice()
      : (modeOf(flow) === 'labeled' ? [runLabel()] : []);
    const url = 'https://api.github.com/repos/' + (flow.github_repo || 'OWNER/REPO') + '/issues';
    const payload = { title: 'Checkout fails with 500 for saved cards', body: 'What happened, the stack trace, and how to reproduce it.' };
    if (labels.length) payload.labels = labels;
    const labelJs = labels.length ? ',\n      labels: ' + JSON.stringify(labels) : '';
    const labelPy = labels.length ? '\n        "labels": ' + JSON.stringify(labels) + ',' : '';
    return {
      curl: 'curl -sS -X POST ' + url + ' \\\n'
        + '  -H "Authorization: Bearer $GITHUB_TOKEN" \\\n'
        + '  -H "Accept: application/vnd.github+json" \\\n'
        + "  -d '" + JSON.stringify(payload, null, 2).replace(/'/g, "'\\''").replace(/\n/g, '\n  ') + "'",
      node: '// Call this from your error handler or alerting hook.\n'
        + 'async function reportToAgents(title, body) {\n'
        + '  const res = await fetch("' + url + '", {\n'
        + '    method: "POST",\n'
        + '    headers: {\n'
        + '      Authorization: `Bearer ${process.env.GITHUB_TOKEN}`,\n'
        + '      Accept: "application/vnd.github+json",\n'
        + '    },\n'
        + '    body: JSON.stringify({\n'
        + '      title,\n'
        + '      body' + labelJs + ',\n'
        + '    }),\n'
        + '  });\n'
        + '  if (!res.ok) throw new Error(`GitHub issue failed: ${res.status}`);\n'
        + '  return (await res.json()).html_url;\n'
        + '}',
      python: '# Call this from your error handler or alerting hook.\n'
        + 'import json, os, urllib.request\n\n'
        + 'def report_to_agents(title, body):\n'
        + '    payload = {\n'
        + '        "title": title,\n'
        + '        "body": body,' + labelPy + '\n'
        + '    }\n'
        + '    req = urllib.request.Request(\n'
        + '        "' + url + '",\n'
        + '        data=json.dumps(payload).encode(),\n'
        + '        headers={\n'
        + '            "Authorization": f"Bearer {os.environ[\'GITHUB_TOKEN\']}",\n'
        + '            "Accept": "application/vnd.github+json",\n'
        + '        },\n'
        + '    )\n'
        + '    with urllib.request.urlopen(req, timeout=15) as res:\n'
        + '        return json.load(res)["html_url"]',
      labels,
    };
  }

  function connectHtml() {
    const flow = S.flow;
    if (!flow) return '';
    const sn = snippet();
    const labeled = modeOf(flow) === 'labeled';
    const tabs = [['curl', 'curl'], ['node', 'Node.js'], ['python', 'Python']];
    const tip = (html) => '<li>' + I.dot + '<span>' + html + '</span></li>';
    return '<p class="iflow-kicker">Step 4 of 5</p>'
      + '<h2 class="iflow-h2">Tell your product to open an issue</h2>'
      + '<p class="iflow-lead">Queue <b>' + esc(flow.queue) + '</b> is watching <b>' + esc(flow.github_repo)
      + '</b>. Wherever your app learns something broke, open an issue there. This is the whole integration:</p>'
      + '<div class="iflow-card"><div class="iflow-tabs" role="tablist">'
      + tabs.map((t) => '<button type="button" role="tab" class="iflow-tab' + (S.tab === t[0] ? ' is-on' : '') + '" data-if="tab" data-tab="' + t[0] + '"'
          + ' aria-selected="' + (S.tab === t[0]) + '">' + t[1] + '</button>').join('')
      + '</div><div class="iflow-codewrap"><pre class="iflow-code" data-if-code>' + esc(sn[S.tab]) + '</pre>'
      + '<button type="button" class="iflow-btn is-small iflow-copy" data-if="copy-code">Copy</button></div></div>'
      + (labeled
        ? callout('info', 'The <code>' + esc(runLabel()) + '</code> label is what starts the agent. An issue without it waits in the queue until you press &#9654; on it.')
        : callout('info', 'Any issue opened on this repo starts ' + fmtGrace(flow.grace_s == null ? 180 : flow.grace_s)
            + '. Add the <code>' + esc(holdLabel()) + '</code> label to keep one out.'))
      + '<h3 class="iflow-h3">The token</h3>'
      + '<p class="iflow-dim" style="margin:0;font-size:12.5px"><code>GITHUB_TOKEN</code> is a '
      + '<a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">fine-grained personal access token</a> '
      + 'limited to this one repository, with <b style="color:var(--if-text)">Issues: Read and write</b>. Keep it in your product&rsquo;s secrets, never in code.</p>'
      + '<h3 class="iflow-h3">What makes an issue an agent can fix</h3><ul class="iflow-tips">'
      + tip('<b>Write it like a bug report.</b> What happened, the error and stack trace, how to reproduce it. The agent knows only what the issue says.')
      + tip('<b>One issue per distinct error.</b> Search open issues for the error&rsquo;s fingerprint before opening another, or a noisy bug files hundreds.')
      + tip(flow.private === false
          ? '<b>This repo is public, so its issues are too.</b> Never include customer data, tokens or internal URLs.'
          : '<b>Leave out secrets and customer data.</b> The agent does not need them, and issues are easy to share by accident.')
      + '</ul>';
  }

  // Step 5 ─ test
  function testRows() {
    const t = S.test;
    const d = (t && t.data) || {};
    const stage = testStage();
    const flow = S.flow;
    const times = (t && t.times) || {};
    const at = (key) => (times[key] && t.startedAt ? '+' + fmtClock((times[key] - t.startedAt) / 1000) : '');
    const rows = [];
    rows.push({
      state: 'ok', title: 'Issue opened on GitHub',
      sub: '<a href="' + esc(t.url) + '" target="_blank" rel="noopener">' + esc(flow.github_repo) + ' #' + t.number + '</a>, exactly as your product would open it.',
      time: '+0:00',
    });
    const inQueue = stage !== 'sent';
    let pickSub = 'Waiting for the queue to see the new issue. This usually takes under a minute.';
    if (!inQueue && t.startedAt && Date.now() - t.startedAt > 90000) {
      pickSub = 'Still waiting. If GitHub is rate-limiting this machine, the next sync can take a few minutes.';
    }
    rows.push({
      state: inQueue ? 'ok' : 'run', title: inQueue ? 'Picked up by ' + flow.queue : 'Queue picks it up',
      sub: inQueue ? 'It is now ticket <b>' + esc(d.ref || t.ref) + '</b>.' : pickSub,
      time: inQueue ? at('queue') : '',
    });
    let agent = { state: '', title: 'Agent starts', sub: '', actions: '' };
    if (stage === 'grace') {
      agent = { state: 'run', title: 'Agent starts in ' + fmtClock(graceLeft()),
        sub: 'New issues wait out a short grace period first, so you can stop one by hand.',
        actions: '<button type="button" class="iflow-btn is-small is-primary" data-if="run-now">Skip the wait</button>' };
    } else if (stage === 'queued') {
      agent = { state: 'run', title: 'Starting an agent', sub: 'WatchTower is launching a worker for this ticket.' };
    } else if (stage === 'parked') {
      agent = { state: 'warn', title: 'Nothing will start this one',
        sub: 'It is in the queue, but ' + esc(d.reason || 'it is not eligible to run') + '.',
        actions: '<button type="button" class="iflow-btn is-small is-primary" data-if="run-now">Run it now</button>' };
    } else if (stage === 'working') {
      agent = { state: 'run', title: 'Agent is working', sub: 'It read the issue and is doing what it says.',
        actions: d.session_id ? '<a class="iflow-btn is-small" href="/?session=' + encodeURIComponent(d.session_id)
          + '" target="_blank" rel="noopener">Watch it work</a>' : '' };
    } else if (stage === 'blocked') {
      agent = { state: 'warn', title: 'The agent has a question',
        sub: esc(d.question || 'It is waiting for an answer.') + ' Answer it on the ticket in Queues.',
        actions: '<button type="button" class="iflow-btn is-small" data-if="open-panel">Open ' + esc(flow.queue) + '</button>' };
    } else if (stage === 'done') {
      agent = { state: 'ok', title: 'Agent worked on it', sub: '',
        actions: d.session_id ? '<a class="iflow-btn is-small" href="/?session=' + encodeURIComponent(d.session_id)
          + '" target="_blank" rel="noopener">Read the session</a>' : '' };
    }
    agent.time = stage === 'working' || stage === 'blocked' || stage === 'done' ? at('agent') : '';
    rows.push(agent);
    rows.push({
      state: stage === 'done' ? 'ok' : '', title: stage === 'done' ? 'Issue closed' : 'Issue gets closed',
      sub: stage === 'done' ? (d.summary ? '<div class="iflow-quote">' + esc(d.summary) + '</div>' : 'The agent closed the issue on GitHub.') : '',
      time: stage === 'done' ? at('done') : '',
    });
    return rows;
  }

  function graceLeft() {
    const t = S.test;
    if (!t || !t.data || t.data.stage !== 'grace') return 0;
    const elapsed = (Date.now() - (t.dataAt || Date.now())) / 1000;
    return Math.max(0, (t.data.grace_remaining_s || 0) - elapsed);
  }

  function timelineHtml() {
    return '<ol class="iflow-tl">' + testRows().map((r) => {
      const cls = r.state === 'ok' ? 'is-done' : r.state ? 'is-active' : 'is-todo';
      return '<li class="' + cls + '">' + stateDot(r.state)
        + '<div class="iflow-tl-main"><div class="iflow-tl-title">' + esc(r.title) + '</div>'
        + (r.sub ? '<div class="iflow-tl-sub">' + r.sub + '</div>' : '')
        + (r.actions ? '<div class="iflow-tl-actions">' + r.actions + '</div>' : '')
        + '</div><span class="iflow-tl-time">' + esc(r.time || '') + '</span></li>';
    }).join('') + '</ol>';
  }

  function testHtml() {
    const flow = S.flow;
    if (!flow) return '';
    const t = S.test;
    const head = '<p class="iflow-kicker">Step 5 of 5</p>';
    if (!t || !t.number) {
      return head + '<h2 class="iflow-h2">Send one through</h2>'
        + '<p class="iflow-lead">See the whole path work before your product depends on it.</p>'
        + '<div class="iflow-card iflow-launch"><div class="iflow-launch-text">'
        + 'This opens <b style="color:var(--if-text)">one real issue</b> on ' + esc(flow.github_repo)
        + ', the same way your product would. An agent picks it up and closes it <b style="color:var(--if-text)">without changing any code</b>. '
        + 'It uses a small amount of your model quota.</div>'
        + '<button type="button" class="iflow-btn is-go is-big" data-if="send-test"' + (t && t.sending ? ' disabled' : '') + '>'
        + (t && t.sending ? 'Opening the issue…' : 'Send a test issue') + '</button></div>'
        + (t && t.error ? callout('bad', esc(t.error)) : '');
    }
    const stage = testStage();
    return head + '<h2 class="iflow-h2">' + (stage === 'done' ? 'It works.' : 'Watch it travel') + '</h2>'
      + '<p class="iflow-lead">' + (stage === 'done'
          ? 'A GitHub issue went in, an agent handled it, and the issue is closed. Your product can do the same thing now.'
          : 'This is live. Each step fills in as the test issue moves through.') + '</p>'
      + '<div data-if-timeline>' + timelineHtml() + '</div>'
      + (t.error ? callout('warn', esc(t.error)) : '');
  }

  // Finish ─ where it lives
  function finishHtml() {
    const flow = S.flow;
    const tested = testStage() === 'done';
    const labeled = modeOf(flow) === 'labeled';
    return '<div class="iflow-finish"><div class="iflow-finish-badge">' + I.tick + '</div>'
      + '<h2 class="iflow-h2">' + (tested ? 'Your flow is live' : 'Your flow is set up') + '</h2>'
      + '<p class="iflow-lead">' + (labeled
          ? 'Issues on <b>' + esc(flow.github_repo) + '</b> tagged <code>' + esc(runLabel()) + '</code> are worked by queue <b>' + esc(flow.queue) + '</b>.'
          : 'Every new issue on <b>' + esc(flow.github_repo) + '</b> is worked by queue <b>' + esc(flow.queue) + '</b>.')
      + (tested ? '' : (S.test && S.test.number
          ? ' Test issue #' + S.test.number + ' is still on its way through; follow it in Queues.'
          : ' No test was run. Open this wizard again any time to send one.')) + '</p></div>'
      + '<div class="iflow-card iflow-where"><div class="iflow-where-art" aria-hidden="true">'
      + '<span><i></i>Sessions</span><span class="is-on"><i></i>Queues</span>'
      + '<span style="margin-left:14px" class="is-on"><i></i>' + esc(flow.queue) + '</span></div>'
      + '<div><b>It lives in Queues, not in this wizard.</b>'
      + '<ul><li><b style="color:var(--if-text)">Queues &rarr; ' + esc(flow.queue) + '</b> shows every issue as a ticket, with the agent&rsquo;s session and its result.</li>'
      + '<li>The gear on that queue changes how many agents run, the model, and auto-drain.</li>'
      + '<li>The bolt at the top of Queues opens this wizard again for another repo.</li></ul></div></div>'
      + '<div class="iflow-finish-actions">'
      + '<button type="button" class="iflow-btn is-go is-big" data-if="open-panel">Open ' + esc(flow.queue) + ' in Queues' + I.arrow + '</button>'
      + '</div>';
  }

  // ── Data ───────────────────────────────────────────────────────────
  async function loadStatus(refresh) {
    S.statusLoading = true; S.statusErr = '';
    if (S.step === 'intro' && $scrim) { if (S.status) render(); else renderFoot(); }
    try {
      S.status = await api(API + '/status' + (refresh ? '?refresh=1' : ''));
    } catch (err) {
      S.statusErr = err.message || 'Could not reach the CCC server';
    }
    S.statusLoading = false;
    resumeIfPossible();
    if ($scrim && (S.step === 'intro' || S.step === 'repo')) render();
  }

  // Reopen onto an unfinished run only while its queue still exists.
  function resumeIfPossible() {
    if (S.flow || !S.pendingResume || !S.status) return;
    const saved = S.pendingResume;
    S.pendingResume = null;
    const live = (S.status.flows || []).find((f) => f.queue === saved.flow.queue);
    if (!live) { try { localStorage.removeItem(LS_KEY); } catch (_) {} return; }
    S.flow = Object.assign({}, saved.flow, { mode: live.mode, grace_s: live.grace_s, workers: live.workers, labels: live.labels });
    S.step = saved.step === 'test' || saved.test ? 'test' : 'connect';
    if (saved.test && saved.test.number) {
      S.test = Object.assign({ data: null, times: {} }, saved.test);
      startPoll();
    }
    render();
  }

  async function loadInfo(githubRepo, repoPath) {
    S.sel = { github_repo: githubRepo, repo_path: repoPath || '' };
    S.info = null; S.infoErr = ''; S.infoLoading = true;
    patchRepo();
    const mine = S.sel;
    try {
      const qs = new URLSearchParams({ github_repo: githubRepo, repo_path: repoPath || '' });
      const info = await api(API + '/repo?' + qs.toString());
      if (S.sel !== mine) return;   // a later pick won the race
      S.info = info;
      S.sel.github_repo = info.github_repo;
      if (info.local && info.local.path) S.sel.repo_path = info.local.path;
      S.form.queue = info.suggested_queue || '';
      S.form.mode = info.recommended_mode || 'labeled';
      S.form.hold = true; S.form.ack = false; S.creating = null;
    } catch (err) {
      if (S.sel !== mine) return;
      S.infoErr = err.message || 'Could not read that repo';
    }
    S.infoLoading = false;
    patchRepo();
  }

  // Re-render only what a repo pick changes, so the grid keeps its scroll.
  function patchRepo() {
    if (!$scrim || S.step !== 'repo') return;
    const infoEl = q('[data-if-info]');
    if (infoEl) infoEl.innerHTML = repoInfoHtml();
    const sel = S.sel || {};
    $scrim.querySelectorAll('.iflow-repo').forEach((el) => {
      el.classList.toggle('is-on', el.getAttribute('data-repo').toLowerCase() === String(sel.github_repo || '').toLowerCase()
        && el.getAttribute('data-path') === sel.repo_path);
    });
    renderPipe(); renderSteps(); renderFoot();
    if (infoEl && (S.info || S.infoErr)) infoEl.scrollIntoView({ block: 'nearest', behavior: reducedMotion() ? 'auto' : 'smooth' });
  }

  async function createQueue() {
    const info = S.info;
    const f = S.form;
    const name = queueName();
    if (!info || !queueVerdict().ok || (S.creating && S.creating.busy)) return;
    const n = info.open_issues;
    const run = runLabel();
    const holding = f.mode === 'all' && f.hold && n > 0;
    const plan = [{ id: 'queue', label: 'Create queue ' + name }];
    if (f.mode === 'labeled') plan.push({ id: 'prepare', label: 'Add the ' + run + ' label to ' + info.github_repo });
    if (holding) plan.push({ id: 'prepare', label: 'Mark the ' + n + ' open ' + (n === 1 ? 'issue' : 'issues') + ' to be left alone' });
    if (f.mode === 'all') plan.push({ id: 'drain', label: 'Turn on auto-drain' });
    // A retry keeps the steps that already succeeded for this same queue.
    const prev = S.creating && S.creating.queue === name ? S.creating.steps : [];
    const steps = plan.map((p) => {
      const old = prev.find((o) => o.id === p.id && o.state === 'ok');
      return { id: p.id, label: p.label, state: old ? 'ok' : '', error: '' };
    });
    S.creating = { queue: name, steps, busy: true };
    const paint = () => {
      const el = q('[data-if-progress]');
      if (el && S.step === 'queue') {
        const tmp = document.createElement('div');
        tmp.innerHTML = queueHtml();
        const next = tmp.querySelector('[data-if-progress]');
        el.innerHTML = next ? next.innerHTML : '';
        el.scrollIntoView({ block: 'nearest' });
      }
      renderFoot();
    };
    render();
    const ops = {
      queue: () => post('/api/queue/config', {
        queue: name, backend: 'github', github_repo: info.github_repo,
        repo_path: (info.local && info.local.path) || '', desired_workers: f.workers,
        auto_drain: false, product_gate: false, claim_types: [], engine: '',
        // Set when the repo still has an archived queue: WatchTower then
        // divides the repo's issues by label, and only the catch-all queue
        // receives a plain, unlabelled issue.
        queue_label: info.queue_label || '',
      }),
      prepare: () => post(API + '/prepare', { queue: name, mode: f.mode, hold_existing: holding }),
      drain: () => post('/api/queue/drain', { queue: name, auto_drain: true }),
    };
    for (const step of steps) {
      if (step.state === 'ok') continue;
      step.state = 'run'; step.error = ''; paint();
      try {
        await ops[step.id]();
        step.state = 'ok';
      } catch (err) {
        step.state = 'err'; step.error = err.message || 'Failed';
        S.creating.busy = false;
        paint();
        return;
      }
      paint();
    }
    S.creating.busy = false;
    S.flow = {
      queue: name, github_repo: info.github_repo, repo_path: (info.local && info.local.path) || '',
      mode: f.mode, grace_s: 180, workers: f.workers, private: !!info.private,
      labels: f.mode === 'labeled' ? [runLabel()] : [],
    };
    persist();
    setTimeout(() => { if (S.step === 'queue') go('connect'); }, 650);
  }

  function useFlow(queue, step) {
    const live = ((S.status && S.status.flows) || []).find((f) => f.queue === queue);
    if (!live) return;
    stopPoll();
    S.flow = Object.assign({}, live, { private: S.info && S.info.github_repo === live.github_repo ? !!S.info.private : null });
    S.test = null; S.finished = false;
    go(step === 'test' ? 'test' : 'connect');
  }

  // ── Test run ───────────────────────────────────────────────────────
  async function sendTest() {
    if (!S.flow || (S.test && (S.test.sending || S.test.number))) return;
    S.test = { sending: true };
    render();
    try {
      const res = await post(API + '/test', { queue: S.flow.queue });
      S.test = { number: res.number, url: res.url, ref: res.ref, startedAt: Date.now(), data: null, times: {} };
      if (res.grace_s != null) S.flow.grace_s = res.grace_s;
      persist();
      startPoll();
    } catch (err) {
      S.test = { error: err.message || 'Could not open the test issue' };
    }
    render();
  }

  function noteStage(stage) {
    const t = S.test;
    if (!t) return;
    t.times = t.times || {};
    const now = Date.now();
    if (stage !== 'sent' && !t.times.queue) t.times.queue = now;
    if ((stage === 'working' || stage === 'blocked' || stage === 'done') && !t.times.agent) t.times.agent = now;
    if (stage === 'done' && !t.times.done) t.times.done = now;
  }

  async function pollTest() {
    const t = S.test;
    if (!t || !t.number || !S.flow) return;
    try {
      const qs = new URLSearchParams({ queue: S.flow.queue, number: String(t.number) });
      const data = await api(API + '/test?' + qs.toString());
      if (S.test !== t) return;
      const before = testStage();
      const hadData = !!t.data;
      const prev = t.data;
      // The queue list can lose sight of a ticket for a moment (a list
      // refresh, or the gap right after the agent closes the issue). Once the
      // queue has picked the ticket up, "not listed" is not news: keep the
      // stage we had and let the next poll settle it.
      if (prev && prev.stage && prev.stage !== 'sent' && data.stage === 'sent') {
        data.stage = prev.stage;
        ['grace_remaining_s', 'question', 'reason'].forEach((k) => { if (data[k] == null && prev[k] != null) data[k] = prev[k]; });
      }
      // The agent's session is worth keeping after the row that named it is gone.
      if (prev) ['session_id', 'worker'].forEach((k) => { if (!data[k] && prev[k]) data[k] = prev[k]; });
      t.data = data; t.dataAt = Date.now(); t.error = '';
      noteStage(data.stage);
      persist();
      if (data.stage === 'done') {
        stopPoll();
        if (before !== 'done' && hadData) celebrate();
      }
      if (S.step === 'test' && !S.finished) {
        if (before !== data.stage || !hadData) render(); else patchTimeline();
      }
    } catch (err) {
      if (S.test === t) { t.error = 'Lost contact with the CCC server. Retrying…'; patchTimeline(); }
    }
  }

  function patchTimeline() {
    const el = q('[data-if-timeline]');
    if (el && S.step === 'test' && !S.finished) el.innerHTML = timelineHtml();
  }

  function startPoll() {
    stopPoll();
    pollTest();
    pollTimer = setInterval(pollTest, POLL_MS);
    // The grace countdown ticks every second between polls.
    tickTimer = setInterval(() => { if (testStage() === 'grace') patchTimeline(); }, 1000);
  }
  function stopPoll() {
    if (pollTimer) clearInterval(pollTimer);
    if (tickTimer) clearInterval(tickTimer);
    pollTimer = null; tickTimer = null;
  }

  async function runNow(btn) {
    const t = S.test;
    if (!t || !t.number) return;
    btn.disabled = true;
    try {
      await post('/api/ux-fixes/run', { ref: (t.data && t.data.ref) || t.ref });
      pollTest();
    } catch (err) {
      t.error = err.message || 'Could not start it';
      btn.disabled = false;
      render();
    }
  }

  // ── Handing over to the Queues board ───────────────────────────────
  function openPanel(queue) {
    const name = queue || (S.flow && S.flow.queue);
    if (!name) return;
    try {
      localStorage.setItem('ccc-q2-queue', name);
      localStorage.setItem('ccc-q2-view-all', '0');
      localStorage.removeItem('ccc-q2-ref');
    } catch (_) {}
    if (S.finished) { try { localStorage.removeItem(LS_KEY); } catch (_) {} }
    close();
    // Already on the Queues board.
    if (typeof window.cccQ2SelectQueue === 'function') {
      window.cccQ2SelectQueue(name);
      spotlight(name);
      return;
    }
    // On the dashboard: Queues is a frame layered over it.
    const frame = document.getElementById('cccCoreAppFrame');
    if (frame && typeof window.cccSwitchCoreApp === 'function' && window.cccSwitchCoreApp('queues')) {
      let tries = 0;
      const wait = setInterval(() => {
        tries += 1;
        let win = null;
        try { win = frame.contentWindow; } catch (_) { win = null; }
        if (win && typeof win.cccQ2SelectQueue === 'function' && win.cccIssueFlow) {
          clearInterval(wait);
          win.cccQ2SelectQueue(name);
          win.cccIssueFlow.spotlight(name);
        } else if (tries > 400) {      // a minute: a big board is slow to boot
          clearInterval(wait);
        }
      }, 150);
      return;
    }
    window.location.href = '/q2.html?iflow-spot=' + encodeURIComponent(name);
  }

  // Runs on the Queues board: ring the queue's row and say what it is.
  function spotlight(queue) {
    loadCss();
    const want = String(queue || '').toUpperCase();
    if (!want) return;
    let tries = 0;
    const find = setInterval(() => {
      tries += 1;
      const row = Array.from(document.querySelectorAll('.q2-qrow[data-q2-queue]'))
        .find((el) => String(el.getAttribute('data-q2-queue')).toUpperCase() === want);
      if (row) {
        clearInterval(find);
        // The board may have booted after the first select call; with its
        // rows on screen the queue is known, so this one always lands.
        if (typeof window.cccQ2SelectQueue === 'function') window.cccQ2SelectQueue(want);
        coach(row, want);
      } else if (tries > 360) {        // 90s: a board with thousands of tickets renders late
        clearInterval(find);
      }
    }, 250);
  }

  function coach(row, queue) {
    document.querySelectorAll('.iflow-coach, .iflow-coach-ring').forEach((el) => el.remove());
    try { row.scrollIntoView({ block: 'center' }); } catch (_) {}
    const ring = document.createElement('div');
    ring.className = 'iflow-coach-ring';
    const tip = document.createElement('div');
    tip.className = 'iflow-coach';
    tip.setAttribute('role', 'status');
    tip.innerHTML = '<strong>Your flow lives here</strong>'
      + '<p>Every GitHub issue shows up in <b>' + esc(queue) + '</b> as a ticket. Click one to see the agent&rsquo;s session and result. The gear changes workers, model and auto-drain.</p>'
      + '<button type="button">Got it</button>';
    document.body.appendChild(ring);
    document.body.appendChild(tip);
    // The board re-renders its rows every poll, so follow the row by name.
    const place = () => {
      const live = Array.from(document.querySelectorAll('.q2-qrow[data-q2-queue]'))
        .find((el) => String(el.getAttribute('data-q2-queue')).toUpperCase() === queue);
      if (!live) return;
      const r = live.getBoundingClientRect();
      ring.style.left = (r.left + 2) + 'px'; ring.style.top = (r.top + 1) + 'px';
      ring.style.width = (r.width - 4) + 'px'; ring.style.height = (r.height - 2) + 'px';
      const left = Math.min(r.right + 14, window.innerWidth - 286);
      const top = Math.max(10, Math.min(r.top - 6, window.innerHeight - tip.offsetHeight - 10));
      tip.style.left = Math.max(10, left) + 'px'; tip.style.top = top + 'px';
    };
    place();
    const follow = setInterval(place, 300);
    const done = () => {
      clearInterval(follow); clearTimeout(expire);
      ring.remove(); tip.remove();
      window.removeEventListener('resize', place);
    };
    const expire = setTimeout(done, 20000);
    window.addEventListener('resize', place);
    tip.querySelector('button').addEventListener('click', done);
  }

  // ── Events ─────────────────────────────────────────────────────────
  function go(step) {
    S.step = step;
    if (step === 'test' && S.test && S.test.number && !pollTimer && testStage() !== 'done') startPoll();
    persist();
    render();
    const pane = q('[data-if-pane]');
    if (pane) pane.focus({ preventScroll: true });
  }

  // Finished one flow, starting another: keep what this machine told us.
  function restart() {
    stopPoll();
    const keep = S.status;
    Object.assign(S, fresh());
    S.status = keep;
    try { localStorage.removeItem(LS_KEY); } catch (_) {}
    go('repo');
    loadStatus(true);
  }

  function next() {
    if (S.step === 'intro') { if (S.status && S.status.ready) go('repo'); return; }
    if (S.step === 'repo') { if (repoVerdict().ok) go('queue'); return; }
    if (S.step === 'connect') { go('test'); }
  }
  function back() {
    if (S.step === 'repo') go('intro');
    else if (S.step === 'queue') go('repo');
    else if (S.step === 'test') go('connect');
  }

  function copyText(text, btn) {
    const done = () => {
      if (!btn) return;
      const old = btn.textContent;
      btn.textContent = 'Copied';
      setTimeout(() => { btn.textContent = old; }, 1300);
    };
    const fallback = () => {
      const ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); done(); } catch (_) {}
      ta.remove();
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, fallback);
    } else fallback();
  }

  function onClick(ev) {
    const el = ev.target.closest('[data-if]');
    if (!el || el.disabled) return;
    const act = el.getAttribute('data-if');
    if (act === 'close') close();
    else if (act === 'next') next();
    else if (act === 'back') back();
    else if (act === 'goto') { if (canVisit(el.getAttribute('data-step'))) { S.finished = false; go(el.getAttribute('data-step')); } }
    else if (act === 'recheck') loadStatus(true);
    else if (act === 'copy') copyText(el.getAttribute('data-copy'), el);
    else if (act === 'copy-code') copyText(snippet()[S.tab], el);
    else if (act === 'pick-repo') loadInfo(el.getAttribute('data-repo'), el.getAttribute('data-path'));
    else if (act === 'manual-open') { S.manualOpen = true; render(); }
    else if (act === 'check-manual') {
      const repo = S.manual.github_repo.trim();
      if (repo) loadInfo(repo, S.manual.repo_path.trim());
    }
    else if (act === 'check-path') {
      const input = q('[data-if-in="path-fix"]');
      if (S.sel && input && input.value.trim()) loadInfo(S.sel.github_repo, input.value.trim());
    }
    else if (act === 'use-flow') useFlow(el.getAttribute('data-queue'), el.getAttribute('data-step'));
    else if (act === 'open-existing') openPanel(el.getAttribute('data-queue'));
    else if (act === 'workers') { S.form.workers = parseInt(el.getAttribute('data-n'), 10) || 1; render(); }
    else if (act === 'create') createQueue();
    else if (act === 'tab') { S.tab = el.getAttribute('data-tab'); render(); }
    else if (act === 'send-test') sendTest();
    else if (act === 'run-now') runNow(el);
    else if (act === 'finish') { S.finished = true; render(); if (testStage() === 'done') celebrate(); }
    else if (act === 'open-panel') openPanel();
    else if (act === 'restart') restart();
  }

  function onInput(ev) {
    const el = ev.target.closest('[data-if-in]');
    if (!el) return;
    const key = el.getAttribute('data-if-in');
    if (key === 'filter') {
      S.filter = el.value;
      // Rebuild only the grid so the filter box keeps focus and caret.
      const host = q('[data-if-repos]');
      if (host) {
        const tmp = document.createElement('div');
        tmp.innerHTML = repoHtml();
        const nextGrid = tmp.querySelector('[data-if-repos]');
        host.innerHTML = nextGrid ? nextGrid.innerHTML : '';
      }
    } else if (key === 'manual-repo') { S.manual.github_repo = el.value; }
    else if (key === 'manual-path') { S.manual.repo_path = el.value; }
    else if (key === 'queue') {
      S.form.queue = el.value;
      const hint = el.parentNode.querySelector('.iflow-field-hint');
      if (hint) hint.textContent = 'Tickets are named after it, like ' + (queueName() || 'QUEUE') + '-42 for issue #42.';
      const v = queueVerdict();
      el.classList.toggle('is-bad', !!v.bad);
      renderPipe(); renderFoot();
    } else if (ev.type === 'change' && key === 'mode') { S.form.mode = el.value; render(); }
    else if (ev.type === 'change' && key === 'hold') { S.form.hold = el.checked; render(); }
    else if (ev.type === 'change' && key === 'ack') { S.form.ack = el.checked; renderFoot(); }
  }

  function onKey(ev) {
    if (ev.key === 'Escape') { ev.stopPropagation(); close(); return; }
    if (ev.key === 'Enter' && ev.target.matches && ev.target.matches('[data-if-in="manual-repo"], [data-if-in="manual-path"]')) {
      ev.preventDefault();
      const repo = S.manual.github_repo.trim();
      if (repo) loadInfo(repo, S.manual.repo_path.trim());
      return;
    }
    if (ev.key === 'Enter' && ev.target.matches && ev.target.matches('[data-if-in="path-fix"]')) {
      ev.preventDefault();
      if (S.sel && ev.target.value.trim()) loadInfo(S.sel.github_repo, ev.target.value.trim());
      return;
    }
    if (ev.key !== 'Tab') return;
    // Keep keyboard focus inside the dialog.
    const focusable = Array.from($scrim.querySelectorAll(
      'button:not([disabled]):not([tabindex="-1"]), a[href], input:not([disabled]), [tabindex="0"]'))
      .filter((n) => n.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
  }

  // ── Open / close ───────────────────────────────────────────────────
  function open(opts) {
    opts = opts || {};
    if ($scrim) return;
    lastFocus = document.activeElement;
    const keepTest = S.test && S.test.number ? S.test : null;
    const keepFlow = S.flow;
    Object.assign(S, fresh());
    if (keepFlow && keepTest && !opts.queue) {
      // Same page, wizard reopened mid-test: carry on.
      S.flow = keepFlow; S.test = keepTest; S.step = 'test';
    } else {
      const saved = opts.queue ? null : restore();
      if (saved) S.pendingResume = saved;
    }
    S.wantQueue = opts.queue || '';
    mount();
    render();
    if (S.test && S.test.number && testStage() !== 'done') startPoll();
    loadStatus(false).then(() => {
      if (S.wantQueue && $scrim) { const want = S.wantQueue; S.wantQueue = ''; useFlow(String(want).toUpperCase(), 'test'); }
    });
    const pane = q('[data-if-pane]');
    if (pane) pane.focus({ preventScroll: true });
  }

  function close() {
    if (!$scrim) return;
    stopPoll();
    persist();
    $scrim.remove();
    $scrim = null;
    try { if (lastFocus && document.contains(lastFocus)) lastFocus.focus(); } catch (_) {}
  }

  window.cccIssueFlow = {
    open,
    close,
    isOpen: () => !!$scrim,
    spotlight,
  };

  document.addEventListener('click', (ev) => {
    const el = ev.target.closest && ev.target.closest('[data-issue-flow-open]');
    if (!el) return;
    ev.preventDefault();
    const opts = { queue: el.getAttribute('data-issue-flow-queue') || '' };
    // The Queues board framed inside the dashboard: open over the whole
    // window, not inside the frame.
    try {
      if (window.parent !== window && window.parent.cccIssueFlow) { window.parent.cccIssueFlow.open(opts); return; }
    } catch (_) { /* cross-origin parent: open here */ }
    open(opts);
  });

  function boot() {
    let params;
    try { params = new URLSearchParams(window.location.search || ''); } catch (_) { return; }
    const spot = params.get('iflow-spot');
    if (spot) spotlight(spot);
    if (params.get('issue-flow') === '1') {
      // One-shot: drop the flag so a reload does not reopen the wizard.
      try {
        params.delete('issue-flow');
        const rest = params.toString();
        history.replaceState(history.state, '', window.location.pathname + (rest ? '?' + rest : '') + window.location.hash);
      } catch (_) {}
      open();
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
