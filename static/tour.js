/*
 * First Flight guide (fft) for CCC.
 * Zero-dependency first-run walkthrough. Defines window.cccTour.
 * Does nothing at load time except define the API.
 */
(function () {
  'use strict';

  const DONE_KEY = 'ccc-tour-done';
  const Z_BASE = 100002;

  function lsGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (_) {
      return (window.__fftLs || {})[key] || null;
    }
  }
  function lsSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (_) {
      window.__fftLs = window.__fftLs || {};
      window.__fftLs[key] = String(value);
    }
  }
  function lsRemove(key) {
    try {
      window.localStorage.removeItem(key);
    } catch (_) {
      if (window.__fftLs) delete window.__fftLs[key];
    }
  }

  const SAMPLE_NOTE =
    'Your list is empty right now, so these sample conversations show working, waiting, and idle. They vanish when the guide ends.';

  // One linear first-run story. Titles/bodies are the user-facing copy tests record.
  const STEPS = [
    {
      id: 'welcome',
      kind: 'center',
      eyebrow: 'FIRST FLIGHT',
      title: 'Welcome to Command Center',
      body: 'This guide walks the controls you need on day one: agent CLIs, reviewing existing conversations, creating first conversation, the LHS, the RHS, your first queue, workers, and Delegation. Skip anytime. Replay it from Settings.',
      primary: 'Start the guide',
      ghost: 'Skip for now'
    },
    {
      id: 'cli-setup',
      kind: 'cli',
      title: 'Ensure agent CLIs are installed',
      body: 'Command Center drives the agent CLIs on this machine. A missing CLI can be installed from this step. A logged-out CLI can sign in here. Re-detect when you are done. Skip if you want to do this later.',
      primary: 'Continue'
    },
    {
      id: 'lhs',
      kind: 'spotlight',
      anchor: ['.sidebar', '[data-tour="lhs"]'],
      title: 'The left-hand side (LHS)',
      body: 'The LHS is the session control tower. New session, search, the conversation list, the Workers tab, and Settings all live on this side.'
    },
    {
      id: 'conversations',
      kind: 'spotlight',
      anchor: '[data-tour="session-list"]',
      needsRows: true,
      sampleNote: SAMPLE_NOTE,
      title: 'Reviewing existing conversations',
      body: 'Every row is one conversation. If you already have sessions they show up here, including ones started from a terminal. Reviewing existing conversations starts with this list: pick a row, read its status, open the transcript.'
    },
    {
      id: 'status',
      kind: 'spotlight',
      anchor: '#convList .conv-item',
      title: 'Status tells you who needs you',
      body: 'Working means the agent is busy. Waiting means it asked you something and is holding. Idle means the turn finished. You only babysit the waiting ones.'
    },
    {
      id: 'transcript',
      kind: 'spotlight',
      anchor: '#convList .conv-item',
      title: 'Open a transcript',
      body: 'Click any conversation to open the full transcript: every command, file edit, and reply. If you ever wonder what an agent did, the answer is in here.'
    },
    {
      id: 'search',
      kind: 'spotlight',
      anchor: ['[data-tour="search"]', '#convSearch'],
      title: 'Search the conversation list',
      body: 'Search across every conversation on the LHS. When several agents have each touched many files, this is how you find the one that edited auth.'
    },
    {
      id: 'new-session',
      kind: 'spotlight',
      anchor: ['[data-tour="new-session"]', '#sidebarNewBtn'],
      title: 'Creating first conversation',
      body: 'New session is the real path for creating first conversation. You do not have to spawn anything during this guide. Later: press it, type a small task, pick a folder, run.'
    },
    {
      id: 'composer',
      kind: 'spotlight',
      anchor: ['[data-tour="spawn-bar"]', '#convInputBar', '[data-tour="new-session"]'],
      reveal: 'composer',
      title: 'The composer is where missions start',
      body: 'Type what you want done, pick an engine, press send. Creating first conversation always goes through this composer (or the New session button that opens it).'
    },
    {
      id: 'engine-picker',
      kind: 'spotlight',
      anchor: ['#convInputEngineSelect', '[data-tour="spawn-bar"]', '[data-tour="new-session"]'],
      reveal: 'composer',
      title: 'Pick the engine for this conversation',
      body: 'The engine picker on the composer chooses which installed CLI runs the session. Match it to a CLI you just made ready.'
    },
    {
      id: 'send',
      kind: 'spotlight',
      anchor: ['#convSendBtn', '[data-tour="spawn-bar"]', '[data-tour="new-session"]'],
      reveal: 'composer',
      title: 'Send starts the session',
      body: 'Send launches the agent. The guide does not require a live spawn. When you are ready, this is the control that turns a prompt into a conversation.'
    },
    {
      id: 'settings',
      kind: 'spotlight',
      anchor: ['[data-tour="settings"]', '#settingsBtn'],
      title: 'Settings',
      body: 'Theme, spawn defaults, and this guide live here. Run onboarding and Take the tour both replay these steps.'
    },
    {
      id: 'workers-tab',
      kind: 'spotlight',
      anchor: ['[data-conv-tab="workers"]', '[data-tour="workers"]'],
      reveal: 'workers',
      title: 'Workers live on the LHS',
      body: 'The Workers tab is the lane for sessions that drain your queues. We opened that real surface so you can see the live control, not a screenshot.'
    },
    {
      id: 'workers-lane',
      kind: 'spotlight',
      anchor: ['[data-tour="workers"]', '[data-conv-tab="workers"]'],
      reveal: 'workers',
      title: 'What workers do',
      body: 'Workers pick up queue tickets and run them as conversations. One worker, one ticket at a time. You can start with zero workers and add them when you have a queue.'
    },
    {
      id: 'rhs',
      kind: 'spotlight',
      anchor: ['#statusRail', '[data-tour="status-rail"]', '#statusRailRestoreBtn'],
      reveal: 'rail',
      title: 'The right-hand side (RHS)',
      body: 'The RHS status rail holds Orchestration, Metadata, Queue, Ask, and Log for the selected conversation. Collapse it when you want a wider transcript; restore it from the edge chevron.'
    },
    {
      id: 'queue-tab',
      kind: 'spotlight',
      anchor: ['[data-rail-tab="queue"]', '[data-tour="queue"]'],
      reveal: 'queue',
      title: 'Open the Queue on the RHS',
      body: 'Your first queue lives on this rail tab. We switched the rail to Queue so the live list is on screen before the rest of the coaching.'
    },
    {
      id: 'first-queue',
      kind: 'spotlight',
      anchor: ['#queuePanel', '#statusRailQueuePane', '[data-rail-tab="queue"]'],
      reveal: 'queue',
      title: 'Your first queue',
      body: 'A queue is a list of tickets workers drain. You do not need to file a ticket now. When you are ready, this is where depth, health, and the next ticket show up.'
    },
    {
      id: 'orchestration',
      kind: 'spotlight',
      anchor: ['[data-rail-tab="orchestration"]', '[data-tour="orchestration"]'],
      reveal: 'orchestration',
      title: 'Orchestration playbooks',
      body: 'Orchestration turns the current conversation into a coordinator. Playbooks draft a brief you can send; the session then fans work out through CCC.'
    },
    {
      id: 'delegation',
      kind: 'spotlight',
      anchor: ['[data-orch-playbook="delegate"]', '[data-tour="delegate"]'],
      reveal: 'delegate',
      title: 'Delegation',
      body: 'Delegate is the playbook that hands execution to a separate lane. Tap it later on a real session to draft the brief. You do not need to send it during this guide.'
    },
    {
      id: 'health',
      kind: 'spotlight',
      anchor: ['[data-tour="watchtower"]', '#cccServerStatusChip', '#settingsBtn'],
      title: 'Server health',
      body: 'The status chip and More menu show whether the dashboard, the execution worker, and the WatchTower queue server are up. Green means all three are online.'
    },
    {
      id: 'finale',
      kind: 'center',
      title: 'You are cleared to fly',
      body: 'That is the cockpit: agent CLIs, reviewing existing conversations, creating first conversation, the LHS, the RHS, your first queue, workers, and Delegation.',
      list: [
        'Install any missing agent CLI, then press New session with one small real task.',
        'Open a conversation and read its transcript. Waiting chips need you.',
        'Put a ticket on your first queue, or tap Delegate on a live session to hand work to a lane.'
      ],
      primary: 'Start flying'
    }
  ];

  const STYLE_ID = 'fft-style';
  const CSS = [
    '.fft-backdrop{position:fixed;inset:0;z-index:' + Z_BASE + ';background:rgba(0,0,0,.55);backdrop-filter:blur(2px);-webkit-backdrop-filter:blur(2px);}',
    '.fft-shield{position:fixed;inset:0;z-index:' + (Z_BASE + 1) + ';background:transparent;}',
    '.fft-spot{position:fixed;z-index:' + (Z_BASE + 2) + ';pointer-events:none;border-radius:10px;border:2px solid var(--cyan);box-shadow:0 0 0 200vmax rgba(0,0,0,0.55);transition:top .25s ease,left .25s ease,width .25s ease,height .25s ease;}',
    '.fft-center-card{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:' + (Z_BASE + 3) + ';width:min(440px,92vw);background:var(--surface);border:1px solid var(--border);border-radius:16px;box-shadow:0 30px 70px rgba(0,0,0,.8);padding:24px;color:var(--text);font-family:var(--font-ui);animation:fftPop .25s ease-out;}',
    '.fft-center-card.fft-cli-card{width:min(560px,94vw);max-height:min(88vh,720px);overflow:auto;}',
    '.fft-card{position:fixed;z-index:' + (Z_BASE + 3) + ';width:min(340px,calc(100vw - 24px));background:var(--surface);border:1px solid var(--border);border-radius:12px;box-shadow:0 30px 70px rgba(0,0,0,.8);padding:20px;color:var(--text);font-family:var(--font-ui);animation:fftPop .25s ease-out;}',
    '@keyframes fftPop{from{opacity:0;transform:scale(.94) translate(-50%,-50%);}to{opacity:1;transform:scale(1) translate(-50%,-50%);}}',
    '.fft-card.fft-anim{animation:fftFade .2s ease-out;}',
    '@keyframes fftFade{from{opacity:0;}to{opacity:1;}}',
    '.fft-eyebrow{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:700;margin:0 0 8px;}',
    '.fft-title{font-size:19px;font-weight:700;margin:0 0 10px;color:var(--text);line-height:1.25;}',
    '.fft-card .fft-title{font-size:16px;}',
    '.fft-body{font-size:14px;line-height:1.55;color:var(--text-muted);margin:0 0 14px;}',
    '.fft-body:last-child{margin-bottom:0;}',
    '.fft-list{margin:0 0 4px;padding:0 0 0 4px;list-style:none;counter-reset:fft;}',
    '.fft-list li{counter-increment:fft;position:relative;padding:6px 0 6px 30px;font-size:14px;line-height:1.5;color:var(--text-muted);}',
    '.fft-list li::before{content:counter(fft);position:absolute;left:0;top:6px;width:20px;height:20px;border-radius:50%;background:var(--surface-2);color:var(--accent);font-weight:700;font-size:12px;display:flex;align-items:center;justify-content:center;}',
    '.fft-btnrow{display:flex;align-items:center;gap:8px;margin-top:16px;}',
    '.fft-progress{font-size:12px;color:var(--text-muted);margin-right:auto;font-variant-numeric:tabular-nums;}',
    '.fft-btn{font-family:var(--font-ui);font-size:13px;font-weight:600;border-radius:8px;padding:8px 14px;cursor:pointer;border:1px solid transparent;transition:background .15s,border-color .15s,color .15s;}',
    '.fft-btn-primary{background:var(--accent);color:var(--accent-contrast,#fff);}',
    '.fft-btn-primary:hover{filter:brightness(1.08);}',
    '.fft-btn-ghost{background:transparent;border:1px solid var(--border);color:var(--text-muted);}',
    '.fft-btn-ghost:hover{color:var(--text);border-color:var(--accent);}',
    '.fft-skip{position:absolute;top:14px;right:16px;font-size:12px;color:var(--text-muted);cursor:pointer;background:transparent;border:none;font-family:var(--font-ui);}',
    '.fft-skip:hover{color:var(--text);text-decoration:underline;}',
    '.fft-center-card,.fft-card{position:fixed;}',
    '.fft-cli-list{display:flex;flex-direction:column;gap:8px;margin:4px 0 12px;}',
    '.fft-cli-row{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;padding:10px 12px;border:1px solid var(--border);border-radius:10px;background:var(--bg,#0d1117);}',
    '.fft-cli-name{font-size:14px;font-weight:600;color:var(--text);}',
    '.fft-cli-cmd{font-size:11px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--text-muted);}',
    '.fft-cli-badge{font-size:11px;font-weight:600;padding:3px 8px;border-radius:999px;border:1px solid var(--border);}',
    '.fft-cli-row[data-fft-cli-state="ready"] .fft-cli-badge{color:var(--green);border-color:var(--green);}',
    '.fft-cli-row[data-fft-cli-state="logged-out"] .fft-cli-badge{color:var(--orange);border-color:var(--orange);}',
    '.fft-cli-row[data-fft-cli-state="missing"] .fft-cli-badge{color:var(--text-muted);}',
    '.fft-cli-actions{display:flex;gap:6px;align-items:center;flex-wrap:wrap;}',
    '.fft-cli-install,.fft-cli-login,.fft-cli-redetect{font-family:var(--font-ui);font-size:12px;font-weight:600;border-radius:8px;padding:6px 10px;cursor:pointer;border:1px solid var(--border);background:var(--surface-2);color:var(--text);}',
    '.fft-cli-redetect{display:block;margin:4px auto 0;}',
    '.fft-cli-note{font-size:12px;color:var(--text-muted);margin:0 0 8px;}',
    '.fft-sample{position:relative;}',
    '.fft-sample .fft-sample-tag{position:absolute;top:6px;right:8px;font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:var(--text-muted);}',
    '.fft-sample .fft-sample-title{font-size:13px;font-weight:600;color:var(--text);margin-bottom:6px;}',
    '.fft-sample .fft-sample-chip{display:inline-flex;align-items:center;gap:6px;font-size:11px;color:var(--text-muted);margin-bottom:6px;}',
    '.fft-sample .fft-sample-dot{width:8px;height:8px;border-radius:50%;display:inline-block;}',
    '.fft-sample .fft-sample-summary{font-size:12px;color:var(--text-muted);line-height:1.4;}',
    '@media (max-width:520px){.fft-card{left:0;right:0;bottom:0;top:auto !important;width:auto;border-radius:16px 16px 0 0;}}'
  ].join('\n');

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const st = document.createElement('style');
    st.id = STYLE_ID;
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  const state = {
    active: false,
    stepIndex: 0,
    nodes: [],
    keyHandler: null,
    repoHandler: null,
    rafPending: false,
    convStash: null,
    samplesInjected: false,
    currentAnchor: null,
    lastReveal: null,
    visited: [],
    cliStatus: null,
    fetchCliStatus: null,
    io: null
  };

  function makeEl(tag, cls) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    return el;
  }

  function track(el) {
    if (el) state.nodes.push(el);
    return el;
  }

  function clearNodes() {
    for (let i = 0; i < state.nodes.length; i++) {
      const n = state.nodes[i];
      if (n && n.parentNode) n.parentNode.removeChild(n);
    }
    state.nodes = [];
  }

  function convList() {
    return document.querySelector('#convList');
  }

  function hasRealRows() {
    return !!document.querySelector('#convList .conv-item:not(.fft-sample)');
  }

  function buildSampleCard(spec) {
    const card = makeEl('div', 'conv-item fft-sample');
    card.setAttribute('data-tour-sample', '1');
    const tag = makeEl('span', 'fft-sample-tag');
    tag.textContent = 'SAMPLE';
    card.appendChild(tag);
    const title = makeEl('div', 'fft-sample-title');
    title.textContent = spec.title;
    card.appendChild(title);
    const chip = makeEl('div', 'fft-sample-chip');
    const dot = makeEl('span', 'fft-sample-dot');
    dot.style.background = spec.dot;
    chip.appendChild(dot);
    const chipLabel = makeEl('span', null);
    chipLabel.textContent = spec.chip;
    chip.appendChild(chipLabel);
    card.appendChild(chip);
    const summary = makeEl('div', 'fft-sample-summary');
    summary.textContent = spec.summary;
    card.appendChild(summary);
    return card;
  }

  function injectSamples() {
    const list = convList();
    if (!list || state.samplesInjected) return;
    if (hasRealRows()) return;
    state.convStash = list.innerHTML;
    const keep = list.querySelector('[data-role="conv-tab-bar"]');
    list.innerHTML = '';
    if (keep) list.appendChild(keep);
    const specs = [
      { title: 'docs-site', chip: 'working', dot: 'var(--green)', summary: 'Rewriting the getting started guide' },
      { title: 'api-server', chip: 'waiting', dot: 'var(--orange)', summary: 'Asked: should I bump the major version?' },
      { title: 'mobile-app', chip: 'idle', dot: 'var(--text-muted)', summary: 'Finished: fixed the login crash' }
    ];
    for (let i = 0; i < specs.length; i++) list.appendChild(buildSampleCard(specs[i]));
    state.samplesInjected = true;
  }

  function restoreSamples() {
    const list = convList();
    if (list) {
      const samples = list.querySelectorAll('.fft-sample');
      for (let i = 0; i < samples.length; i++) {
        const s = samples[i];
        if (s.parentNode) s.parentNode.removeChild(s);
      }
      if (state.convStash !== null) list.innerHTML = state.convStash;
    }
    state.convStash = null;
    state.samplesInjected = false;
  }

  function positionCard(card, rect) {
    if (window.innerWidth <= 520) {
      card.style.top = '';
      card.style.left = '';
      return;
    }
    const margin = 12;
    const gap = 12;
    card.style.left = '0px';
    card.style.top = '0px';
    const cw = card.offsetWidth;
    const ch = card.offsetHeight;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let top;
    let left;
    const spaceBelow = vh - rect.bottom;
    const spaceAbove = rect.top;
    const spaceRight = vw - rect.right;
    const spaceLeft = rect.left;
    if (spaceBelow >= ch + gap) {
      top = rect.bottom + gap;
      left = rect.left + rect.width / 2 - cw / 2;
    } else if (spaceAbove >= ch + gap) {
      top = rect.top - gap - ch;
      left = rect.left + rect.width / 2 - cw / 2;
    } else if (spaceRight >= cw + gap) {
      left = rect.right + gap;
      top = rect.top + rect.height / 2 - ch / 2;
    } else if (spaceLeft >= cw + gap) {
      left = rect.left - gap - cw;
      top = rect.top + rect.height / 2 - ch / 2;
    } else {
      top = rect.bottom + gap;
      left = rect.left;
    }
    if (left < margin) left = margin;
    if (left + cw > vw - margin) left = vw - margin - cw;
    if (top < margin) top = margin;
    if (top + ch > vh - margin) top = vh - margin - ch;
    card.style.left = left + 'px';
    card.style.top = top + 'px';
  }

  function positionSpot(spot, rect) {
    const pad = 6;
    spot.style.top = rect.top - pad + 'px';
    spot.style.left = rect.left - pad + 'px';
    spot.style.width = rect.width + pad * 2 + 'px';
    spot.style.height = rect.height + pad * 2 + 'px';
  }

  function remeasure() {
    if (!state.active || !state.currentAnchor) return;
    const el = state.currentAnchor;
    if (!el || !el.isConnected) return;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    const spot = document.querySelector('.fft-spot');
    const card = document.querySelector('.fft-card');
    if (spot) positionSpot(spot, rect);
    if (card) positionCard(card, rect);
  }

  function onRepoEvent() {
    if (state.rafPending) return;
    state.rafPending = true;
    window.requestAnimationFrame(function () {
      state.rafPending = false;
      remeasure();
    });
  }

  function makeButton(label, cls, onClick) {
    const b = makeEl('button', 'fft-btn ' + cls);
    b.type = 'button';
    b.textContent = label;
    b.addEventListener('click', onClick);
    return b;
  }

  function makeSkipLink() {
    const b = makeEl('button', 'fft-skip');
    b.type = 'button';
    b.textContent = 'Skip guide';
    b.addEventListener('click', function () { end('skip'); });
    return b;
  }

  function renderProgress(container, index) {
    const wrap = makeEl('div', 'fft-progress');
    wrap.textContent = (index + 1) + ' / ' + STEPS.length;
    container.appendChild(wrap);
  }

  function currentStep() {
    return STEPS[state.stepIndex] || null;
  }

  function markVisited(step) {
    if (!step) return;
    const last = state.visited[state.visited.length - 1];
    if (last && last.id === step.id) return;
    state.visited.push({ id: step.id, title: step.title, body: step.body || '' });
  }

  function setVisible(el) {
    if (!el) return;
    el.hidden = false;
    el.removeAttribute('hidden');
    if (el.style) {
      if (el.style.display === 'none') el.style.display = '';
      if (el.style.visibility === 'hidden') el.style.visibility = 'visible';
    }
    let p = el.parentElement;
    let hops = 0;
    while (p && p !== document.body && hops < 8) {
      if (p.hidden) {
        p.hidden = false;
        p.removeAttribute('hidden');
      }
      p = p.parentElement;
      hops += 1;
    }
  }

  function activateRailTab(name) {
    const rail = document.getElementById('statusRail');
    const tab = document.querySelector('[data-rail-tab="' + name + '"]');
    if (tab) {
      setVisible(tab);
      document.querySelectorAll('[data-rail-tab]').forEach(function (btn) {
        const on = btn === tab;
        btn.classList.toggle('is-active', on);
        btn.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      try { tab.click(); } catch (_) {}
    }
    document.querySelectorAll('[data-rail-pane]').forEach(function (pane) {
      const on = pane.getAttribute('data-rail-pane') === name;
      pane.classList.toggle('is-active', on);
      pane.hidden = !on;
      if (on) setVisible(pane);
    });
    if (rail) setVisible(rail);
  }

  const REVEALS = {
    composer: function () {
      // First-run has no session, so the dashboard keeps .conv-input-bar at
      // display:none until New session / updateInputBar() adds .visible.
      const newBtn = document.getElementById('sidebarNewBtn');
      try { if (newBtn) newBtn.click(); } catch (_) {}
      const bar = document.getElementById('convInputBar');
      if (bar) {
        bar.classList.add('visible');
        setVisible(bar);
      }
      const engine = document.getElementById('convInputEngineSelect');
      if (engine) {
        engine.style.display = '';
        setVisible(engine);
      }
      setVisible(document.getElementById('convInput'));
      setVisible(document.getElementById('convSendBtn'));
    },
    workers: function () {
      let tab = document.querySelector('[data-conv-tab="workers"]');
      const list = document.querySelector('#convList');
      if (!tab && list) {
        let bar = list.querySelector('[data-role="conv-tab-bar"]');
        if (!bar) {
          bar = makeEl('div', 'conv-tab-bar');
          bar.setAttribute('data-role', 'conv-tab-bar');
          list.insertBefore(bar, list.firstChild);
        }
        tab = makeEl('button', 'conv-tab');
        tab.type = 'button';
        tab.setAttribute('data-conv-tab', 'workers');
        tab.setAttribute('data-tour', 'workers');
        tab.textContent = 'Workers';
        bar.appendChild(tab);
      }
      if (tab) {
        setVisible(tab);
        document.querySelectorAll('[data-conv-tab]').forEach(function (btn) {
          btn.classList.toggle('is-active', btn === tab);
        });
        try { tab.click(); } catch (_) {}
      }
    },
    rail: function () {
      try { document.body.classList.remove('status-rail-collapsed'); } catch (_) {}
      const rail = document.getElementById('statusRail');
      setVisible(rail);
      const restore = document.getElementById('statusRailRestoreBtn');
      if (restore && rail && (rail.hidden || (rail.getBoundingClientRect && rail.getBoundingClientRect().width === 0))) {
        try { restore.click(); } catch (_) {}
        setVisible(rail);
      }
    },
    queue: function () {
      REVEALS.rail();
      activateRailTab('queue');
      setVisible(document.getElementById('queuePanel'));
      setVisible(document.querySelector('[data-rail-tab="queue"]'));
    },
    orchestration: function () {
      REVEALS.rail();
      activateRailTab('orchestration');
    },
    delegate: function () {
      REVEALS.orchestration();
      let btn = document.querySelector('[data-orch-playbook="delegate"]');
      const host = document.getElementById('orchPlaybooks');
      if (!btn && host) {
        btn = makeEl('button', 'orch-playbook');
        btn.type = 'button';
        btn.setAttribute('data-orch-playbook', 'delegate');
        btn.setAttribute('data-tour', 'delegate');
        const title = makeEl('span', 'orch-playbook-title');
        title.textContent = 'Delegate';
        btn.appendChild(title);
        host.appendChild(btn);
      }
      if (btn) setVisible(btn);
    }
  };

  function applyReveal(name) {
    if (!name || !REVEALS[name]) return;
    try { REVEALS[name](); } catch (_) {}
  }

  function resolveAnchor(anchor) {
    const sels = Array.isArray(anchor) ? anchor : [anchor];
    for (let i = 0; i < sels.length; i++) {
      if (!sels[i]) continue;
      const el = document.querySelector(sels[i]);
      if (!el) continue;
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) return { el: el, selector: sels[i], rect: r };
    }
    return null;
  }

  function cliRowState(cli) {
    if (cli && cli.available && cli.logged_in) return 'ready';
    if (cli && cli.available) return 'logged-out';
    return 'missing';
  }

  function renderCliRows(host, clis) {
    host.innerHTML = '';
    const keys = clis ? Object.keys(clis) : [];
    for (let i = 0; i < keys.length; i++) {
      const engine = keys[i];
      const cli = clis[engine] || {};
      const row = makeEl('div', 'fft-cli-row');
      const st = cliRowState(cli);
      row.setAttribute('data-fft-cli', engine);
      row.setAttribute('data-fft-cli-state', st);
      const info = makeEl('div', null);
      const name = makeEl('div', 'fft-cli-name');
      name.textContent = cli.name || engine;
      info.appendChild(name);
      const cmd = makeEl('div', 'fft-cli-cmd');
      cmd.textContent = cli.command ? 'command: ' + cli.command : engine;
      info.appendChild(cmd);
      row.appendChild(info);
      const actions = makeEl('div', 'fft-cli-actions');
      const badge = makeEl('span', 'fft-cli-badge');
      badge.textContent = st === 'ready' ? 'Ready' : (st === 'logged-out' ? 'Needs login' : 'Not installed');
      actions.appendChild(badge);
      if (st === 'missing') {
        const install = makeEl('button', 'fft-cli-install');
        install.type = 'button';
        install.textContent = 'Install';
        install.addEventListener('click', function () { runInstall(engine, cli, install); });
        actions.appendChild(install);
      } else if (st === 'logged-out') {
        const login = makeEl('button', 'fft-cli-login');
        login.type = 'button';
        login.textContent = 'Log in';
        login.addEventListener('click', function () { runLogin(engine, cli, login); });
        actions.appendChild(login);
      }
      row.appendChild(actions);
      host.appendChild(row);
    }
  }

  function copyText(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text);
      }
    } catch (_) {}
  }

  function runInstall(engine, cli, btn) {
    const io = state.io || {};
    const done = function (ok) {
      if (btn) btn.textContent = ok ? 'Install started' : 'Install';
    };
    if (typeof io.installEngine === 'function') {
      Promise.resolve(io.installEngine(engine)).then(function (res) { done(res && res.ok !== false); }).catch(function () { done(false); });
      return;
    }
    fetch('/api/onboarding/install-terminal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ engine: engine })
    }).then(function (r) { return r.json().catch(function () { return {}; }); }).then(function (data) {
      if (!(data && data.ok) && cli && cli.install_instruction) copyText(cli.install_instruction);
      done(!!(data && data.ok));
    }).catch(function () {
      if (cli && cli.install_instruction) copyText(cli.install_instruction);
      done(false);
    });
  }

  function runLogin(engine, cli, btn) {
    const io = state.io || {};
    const done = function (ok) {
      if (btn) btn.textContent = ok ? 'Login started' : 'Log in';
    };
    if (typeof io.loginEngine === 'function') {
      Promise.resolve(io.loginEngine(engine)).then(function (res) { done(res && res.ok !== false); }).catch(function () { done(false); });
      return;
    }
    fetch('/api/onboarding/login-terminal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ engine: engine })
    }).then(function (r) { return r.json().catch(function () { return {}; }); }).then(function (data) {
      if (data && data.ok) { done(true); return; }
      if (data && data.inline_login) {
        return fetch('/api/onboarding/login/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ engine: engine })
        }).then(function (r2) { return r2.json().catch(function () { return {}; }); }).then(function (started) {
          done(!!(started && started.ok));
        });
      }
      if (cli && cli.login_instruction) copyText(cli.login_instruction);
      done(false);
    }).catch(function () {
      if (cli && cli.login_instruction) copyText(cli.login_instruction);
      done(false);
    });
  }

  function loadCliStatus() {
    if (state.cliStatus && state.cliStatus.clis) {
      return Promise.resolve(state.cliStatus);
    }
    if (typeof state.fetchCliStatus === 'function') {
      return Promise.resolve(state.fetchCliStatus()).then(function (data) {
        state.cliStatus = data;
        return data;
      });
    }
    const io = state.io || {};
    if (typeof io.getOnboardingStatus === 'function') {
      return Promise.resolve(io.getOnboardingStatus()).then(function (data) {
        state.cliStatus = data;
        return data;
      });
    }
    return fetch('/api/onboarding/status')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        state.cliStatus = data;
        return data;
      })
      .catch(function () { return { clis: {} }; });
  }

  function redetectCli(listHost) {
    const fetchFn = typeof state.fetchCliStatus === 'function'
      ? state.fetchCliStatus
      : function () {
        return fetch('/api/onboarding/status').then(function (r) { return r.json(); });
      };
    return Promise.resolve(fetchFn()).then(function (data) {
      if (data) state.cliStatus = data;
      if (listHost) renderCliRows(listHost, (data && data.clis) || {});
      return data;
    }).catch(function () { return state.cliStatus; });
  }

  function decorateCard(card, step) {
    card.setAttribute('data-fft-step', step.id);
    card.setAttribute('data-fft-kind', step.kind);
  }

  function footerRow(step, opts) {
    const row = makeEl('div', 'fft-btnrow');
    renderProgress(row, state.stepIndex);
    if (state.stepIndex > 0 && !(opts && opts.hideBack)) {
      row.appendChild(makeButton('Back', 'fft-btn-ghost', function () { back(); }));
    }
    const label = step.primary || (state.stepIndex >= STEPS.length - 1 ? 'Start flying' : 'Next');
    row.appendChild(makeButton(label, 'fft-btn-primary', function () { next(); }));
    return row;
  }

  function showCenter(step) {
    clearNodes();
    state.currentAnchor = null;
    state.lastReveal = { id: step.id, visible: true, beforeSpotlight: true, selector: null };
    const backdrop = track(makeEl('div', 'fft-backdrop'));
    document.body.appendChild(backdrop);
    const card = track(makeEl('div', 'fft-center-card' + (step.kind === 'cli' ? ' fft-cli-card' : '')));
    decorateCard(card, step);
    if (state.stepIndex > 0) card.appendChild(makeSkipLink());
    if (step.eyebrow) {
      const eyebrow = makeEl('div', 'fft-eyebrow');
      eyebrow.textContent = step.eyebrow;
      card.appendChild(eyebrow);
    }
    const title = makeEl('div', 'fft-title');
    title.textContent = step.title;
    card.appendChild(title);
    const body = makeEl('p', 'fft-body');
    body.textContent = step.body;
    card.appendChild(body);
    if (step.kind === 'cli') {
      const list = makeEl('div', 'fft-cli-list');
      list.textContent = 'Scanning local environment...';
      card.appendChild(list);
      const redetect = makeEl('button', 'fft-cli-redetect');
      redetect.type = 'button';
      redetect.textContent = 'Re-detect status';
      redetect.addEventListener('click', function () {
        redetect.textContent = 'Scanning...';
        redetectCli(list).then(function () { redetect.textContent = 'Re-detect status'; });
      });
      card.appendChild(redetect);
      loadCliStatus().then(function (data) {
        if (!list.isConnected) return;
        renderCliRows(list, (data && data.clis) || {});
      });
    }
    if (step.list && step.list.length) {
      const ol = makeEl('ol', 'fft-list');
      for (let i = 0; i < step.list.length; i++) {
        const li = makeEl('li', null);
        li.textContent = step.list[i];
        ol.appendChild(li);
      }
      card.appendChild(ol);
    }
    if (step.ghost && state.stepIndex === 0) {
      const row = makeEl('div', 'fft-btnrow');
      renderProgress(row, 0);
      row.appendChild(makeButton(step.ghost, 'fft-btn-ghost', function () { end('skip'); }));
      row.appendChild(makeButton(step.primary || 'Next', 'fft-btn-primary', function () { next(); }));
      card.appendChild(row);
    } else {
      card.appendChild(footerRow(step));
    }
    document.body.appendChild(card);
    markVisited(step);
  }

  function showSpotlight(step, direction, hops) {
    if (step.needsRows) {
      if (!document.querySelector('#convList .conv-item')) {
        try { injectSamples(); } catch (_) {}
      }
    }
    if (step.reveal) applyReveal(step.reveal);
    const hit = resolveAnchor(step.anchor);
    const el = hit && hit.el;
    let rect = hit && hit.rect;
    if (el) {
      try { el.scrollIntoView({ block: 'center', behavior: 'instant' }); } catch (_) {
        try { el.scrollIntoView(); } catch (__) {}
      }
      rect = el.getBoundingClientRect();
    }
    const visible = !!(el && rect && rect.width > 0 && rect.height > 0);
    state.lastReveal = {
      id: step.id,
      selector: Array.isArray(step.anchor) ? step.anchor[0] : step.anchor,
      matched: hit ? hit.selector : null,
      visible: visible,
      beforeSpotlight: true
    };
    if (!visible) {
      const nextIndex = state.stepIndex + (direction >= 0 ? 1 : -1);
      if ((hops || 0) > STEPS.length) return;
      showStep(nextIndex, direction, (hops || 0) + 1);
      return;
    }
    state.currentAnchor = el;
    clearNodes();
    const shield = track(makeEl('div', 'fft-shield'));
    document.body.appendChild(shield);
    const spot = track(makeEl('div', 'fft-spot'));
    document.body.appendChild(spot);
    positionSpot(spot, rect);
    const card = track(makeEl('div', 'fft-card fft-anim'));
    decorateCard(card, step);
    card.appendChild(makeSkipLink());
    const title = makeEl('div', 'fft-title');
    title.textContent = step.title;
    card.appendChild(title);
    const body = makeEl('p', 'fft-body');
    body.textContent = step.body;
    card.appendChild(body);
    if (step.needsRows && step.sampleNote && state.samplesInjected) {
      const note = makeEl('p', 'fft-body');
      note.textContent = step.sampleNote;
      card.appendChild(note);
    }
    card.appendChild(footerRow(step));
    document.body.appendChild(card);
    positionCard(card, rect);
    markVisited(step);
  }

  function showStep(index, direction, hops) {
    if (index >= STEPS.length) {
      end('done');
      return;
    }
    if (index < 0) {
      state.stepIndex = 0;
      showCenter(STEPS[0]);
      return;
    }
    state.stepIndex = index;
    const step = STEPS[index];
    if (!step) {
      end('done');
      return;
    }
    if (step.kind === 'center' || step.kind === 'cli') {
      showCenter(step);
      return;
    }
    showSpotlight(step, direction >= 0 ? 1 : -1, hops || 0);
  }

  function next() {
    if (!state.active) return;
    if (state.stepIndex >= STEPS.length - 1) {
      end('done');
      return;
    }
    showStep(state.stepIndex + 1, 1);
  }

  function back() {
    if (!state.active) return;
    showStep(state.stepIndex - 1, -1);
  }

  function onKeyDown(e) {
    if (!state.active) return;
    if (e && (e.isComposing || e.keyCode === 229)) return;
    const key = e.key;
    const tag = (e.target && e.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
      if (key === 'Escape') {
        e.stopPropagation();
        e.preventDefault();
        end('skip');
      }
      return;
    }
    if (key === 'Escape') {
      e.stopPropagation();
      e.preventDefault();
      end('skip');
      return;
    }
    if (key === 'ArrowRight' || key === 'Enter') {
      e.stopPropagation();
      e.preventDefault();
      next();
      return;
    }
    if (key === 'ArrowLeft') {
      e.stopPropagation();
      e.preventDefault();
      back();
      return;
    }
    e.stopPropagation();
  }

  function persist(reason) {
    let flag;
    if (reason === 'skip' || reason === 'error') flag = 'skipped';
    else flag = 'done';
    lsSet(DONE_KEY, flag);
    try {
      const io = state.io || {};
      if (typeof io.completeOnboarding === 'function') {
        io.completeOnboarding();
      } else if (typeof fetch === 'function') {
        fetch('/api/onboarding/complete', { method: 'POST' }).catch(function () {});
      }
    } catch (_) {}
  }

  function teardown() {
    if (state.keyHandler) {
      document.removeEventListener('keydown', state.keyHandler, true);
      state.keyHandler = null;
    }
    if (state.repoHandler) {
      window.removeEventListener('resize', state.repoHandler, { passive: true });
      window.removeEventListener('scroll', state.repoHandler, { capture: true, passive: true });
      state.repoHandler = null;
    }
    clearNodes();
    try { restoreSamples(); } catch (_) {}
    window.__cccTourActive = false;
    state.active = false;
    state.currentAnchor = null;
  }

  function start(opts) {
    const options = opts || {};
    if (state.active && !options.force) return;
    const done = lsGet(DONE_KEY);
    if (done && !options.force) return;
    if (state.active) teardown();
    ensureStyle();
    state.active = true;
    state.stepIndex = 0;
    state.convStash = null;
    state.samplesInjected = false;
    state.currentAnchor = null;
    state.lastReveal = null;
    state.visited = [];
    state.cliStatus = options.cliStatus || null;
    state.fetchCliStatus = typeof options.fetchCliStatus === 'function' ? options.fetchCliStatus : null;
    state.io = options.io || null;
    window.__cccTourActive = true;
    state.keyHandler = onKeyDown;
    document.addEventListener('keydown', state.keyHandler, true);
    state.repoHandler = onRepoEvent;
    window.addEventListener('resize', state.repoHandler, { passive: true });
    window.addEventListener('scroll', state.repoHandler, { capture: true, passive: true });
    try {
      showStep(0, 1);
    } catch (_) {
      end('error');
    }
  }

  function end(reason) {
    if (!state.active) {
      window.__cccTourActive = false;
      return;
    }
    persist(reason || 'done');
    teardown();
    state.stepIndex = 0;
  }

  function skip() {
    end('skip');
  }

  function getState() {
    const step = currentStep();
    return {
      active: state.active,
      index: state.stepIndex,
      total: STEPS.length,
      step: step ? { id: step.id, title: step.title, body: step.body, kind: step.kind } : null,
      revealed: state.lastReveal,
      visited: state.visited.slice()
    };
  }

  function setCliStatus(payload) {
    state.cliStatus = payload;
    const list = document.querySelector('.fft-cli-list');
    if (list && payload) renderCliRows(list, payload.clis || {});
  }

  window.cccTour = {
    start: start,
    end: end,
    next: next,
    back: back,
    skip: skip,
    getState: getState,
    setCliStatus: setCliStatus
  };
})();
