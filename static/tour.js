/*
 * First Flight Tour (fft) for CCC.
 * Zero-dependency onboarding tour. Defines window.cccTour = { start, end }.
 * Does nothing at load time except define the API.
 */
(function () {
  'use strict';

  const DONE_KEY = 'ccc-tour-done';
  const Z_BASE = 100002;

  // ---- localStorage helpers (always guarded) ----
  function lsGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (_) {
      return null;
    }
  }
  function lsSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (_) {
      /* ignore */
    }
  }

  // ---- Step data (copy is final, verbatim) ----
  const WELCOME = {
    eyebrow: 'FIRST FLIGHT',
    title: 'Welcome to Command Center',
    body: 'This is your control tower for AI coding agents. The tour takes two minutes and shows you the handful of controls that matter. No manual, no jargon.',
    primary: 'Start the tour',
    ghost: 'Skip for now'
  };

  const FORK = {
    title: 'Pick your runway',
    body: 'One question so we show you the right things.',
    choices: [
      {
        path: 'newcomer',
        label: "I'm new to agent fleets",
        sub: 'Show me the basics: sessions, statuses, my first spawn.'
      },
      {
        path: 'multi',
        label: 'I already run multiple engines',
        sub: 'Show me fleet tools: engines, queues, Watchtower, group chats.'
      }
    ]
  };

  const SAMPLE_NOTE =
    'Your deck is empty right now, so we parked three practice planes here. They vanish when the tour ends.';

  const PATHS = {
    newcomer: {
      steps: [
        {
          anchor: '[data-tour="session-list"]',
          needsRows: true,
          sampleNote: SAMPLE_NOTE,
          title: 'The flight deck',
          body: 'Every row in this list is one session: an AI agent working in one of your repos. Sessions appear here automatically when they start, whether you launch them from CCC or from a terminal.'
        },
        {
          anchor: '#convList .conv-item',
          title: 'Who needs you',
          body: 'The status chip is the whole game. Working means hands off, the agent is busy. Waiting means it asked you something and is holding for an answer. Idle means the turn finished. You only ever babysit the yellow ones.'
        },
        {
          anchor: '#convList .conv-item',
          title: 'Read the black box',
          body: 'Click any row to open the full transcript: every command, every file edit, every reply. If you ever wonder what an agent did, the answer is always in here.'
        },
        {
          anchor: '[data-tour="new-session"]',
          title: 'Your first spawn',
          body: 'This button launches a new agent. Type what you want done, pick a folder, press run. Start small: a README fix is a great first mission.'
        },
        {
          anchor: '[data-tour="watchtower"]',
          title: 'Where health lives',
          body: 'This badge is Watchtower, the tower that watches the tower. It counts queued work and flags sessions that look stuck. Quiet badge, healthy fleet.'
        },
        {
          anchor: '[data-tour="settings"]',
          title: 'The cockpit switches',
          body: 'Theme, defaults, and extras live here. Take the tour is inside too, so you can replay this anytime.'
        }
      ],
      finale: {
        title: 'Cleared for takeoff',
        body: "That's the whole cockpit. Three things to try right now:",
        list: [
          'Press New session and give an agent one small, real task in one of your repos.',
          'Watch the status chip while it works, and answer when it flips to waiting.',
          'Open the row and read the transcript to see exactly how it got there.'
        ],
        button: 'Start flying'
      }
    },
    multi: {
      steps: [
        {
          anchor: '[data-tour="session-list"]',
          needsRows: true,
          sampleNote: SAMPLE_NOTE,
          title: 'One tower, every engine',
          body: 'Every session lands in this one list, whatever runs it: Claude, Codex, Cursor, Hermes and friends. Statuses, transcripts and controls are identical across engines.'
        },
        {
          anchor: ['[data-tour="spawn-bar"]', '[data-tour="new-session"]'],
          title: 'Spawn with intent',
          body: 'Start a new session and the composer appears with engine, model and effort pickers. Spawn defaults in Settings makes your favorite combo the default so you just type missions.'
        },
        {
          anchor: '[data-tour="watchtower"]',
          title: 'Watchtower',
          body: 'The fleet brain. Queues of tickets, worker counts, stuck-session warnings, all behind this badge. Got a plan document? wt import turns it into a queue and your fleet drains it.'
        },
        {
          anchor: '[data-tour="group-chat"]',
          title: 'Group chats',
          body: 'Put several sessions in one room. They coordinate, argue and split the work; you moderate. Good for a big task that needs more than one brain.'
        },
        {
          anchor: '[data-tour="search"]',
          title: 'Find the needle',
          body: 'Search across every session and transcript. When ten agents each touched twenty files, this is how you find the one that edited auth.'
        },
        {
          anchor: '[data-tour="settings"]',
          title: 'Fleet-grade settings',
          body: 'Spawn defaults, network access and federation peers live here. Yes, federation: CCC can see sessions on other machines too. Take the tour is here when you want a replay.'
        }
      ],
      finale: {
        title: 'Tower is yours',
        body: 'You know the deck. Three things to try right now:',
        list: [
          'Spawn two sessions on different engines and watch them run side by side.',
          'Click the Watchtower badge to see your queues, then try wt import on a plan doc.',
          'Start a group chat and let two sessions split one task.'
        ],
        button: 'Back to work'
      }
    }
  };

  // ---- Styles ----
  const STYLE_ID = 'fft-style';
  const CSS = [
    '.fft-backdrop{position:fixed;inset:0;z-index:' + Z_BASE + ';background:rgba(0,0,0,.55);backdrop-filter:blur(2px);-webkit-backdrop-filter:blur(2px);}',
    '.fft-shield{position:fixed;inset:0;z-index:' + (Z_BASE + 1) + ';background:transparent;}',
    '.fft-spot{position:fixed;z-index:' + (Z_BASE + 2) + ';pointer-events:none;border-radius:10px;border:2px solid var(--cyan);box-shadow:0 0 0 200vmax rgba(0,0,0,0.55);transition:top .25s ease,left .25s ease,width .25s ease,height .25s ease;}',
    '.fft-center-card{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:' + (Z_BASE + 3) + ';width:min(440px,92vw);background:var(--surface);border:1px solid var(--border);border-radius:16px;box-shadow:0 30px 70px rgba(0,0,0,.8);padding:24px;color:var(--text);font-family:var(--font-ui);animation:fftPop .25s ease-out;}',
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
    '.fft-btnrow.fft-stack{flex-direction:column;align-items:stretch;}',
    '.fft-dots{display:flex;gap:6px;align-items:center;margin-right:auto;}',
    '.fft-dot{width:6px;height:6px;border-radius:50%;background:var(--border);transition:background .15s;}',
    '.fft-dot.fft-done{background:var(--text-muted);}',
    '.fft-dot.fft-cur{background:var(--accent);}',
    '.fft-btn{font-family:var(--font-ui);font-size:13px;font-weight:600;border-radius:8px;padding:8px 14px;cursor:pointer;border:1px solid transparent;transition:background .15s,border-color .15s,color .15s;}',
    '.fft-btn-primary{background:var(--accent);color:var(--accent-contrast);}',
    '.fft-btn-primary:hover{filter:brightness(1.08);}',
    '.fft-btn-ghost{background:transparent;border:1px solid var(--border);color:var(--text-muted);}',
    '.fft-btn-ghost:hover{color:var(--text);border-color:var(--accent);}',
    '.fft-skip{position:absolute;top:14px;right:16px;font-size:12px;color:var(--text-muted);cursor:pointer;background:transparent;border:none;font-family:var(--font-ui);}',
    '.fft-skip:hover{color:var(--text);text-decoration:underline;}',
    '.fft-choice{display:block;width:100%;text-align:left;background:var(--surface-2);border:1px solid var(--border);border-radius:10px;padding:14px 16px;cursor:pointer;color:var(--text);font-family:var(--font-ui);transition:border-color .15s,background .15s;}',
    '.fft-choice:hover{border-color:var(--accent);background:var(--surface);}',
    '.fft-choice-title{font-size:14px;font-weight:600;margin:0 0 4px;}',
    '.fft-choice-sub{font-size:12.5px;color:var(--text-muted);line-height:1.4;}',
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

  // ---- Tour state ----
  const state = {
    active: false,
    path: null,
    stepIndex: 0,
    nodes: [],
    keyHandler: null,
    repoHandler: null,
    rafPending: false,
    convStash: null,
    samplesInjected: false,
    currentAnchor: null
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

  // ---- Sample cards ----
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
    list.innerHTML = '';

    const specs = [
      {
        title: 'docs-site',
        chip: 'working',
        dot: 'var(--green)',
        summary: 'Rewriting the getting started guide'
      },
      {
        title: 'api-server',
        chip: 'waiting',
        dot: 'var(--orange)',
        summary: 'Asked: should I bump the major version?'
      },
      {
        title: 'mobile-app',
        chip: 'idle',
        dot: 'var(--text-muted)',
        summary: 'Finished: fixed the login crash'
      }
    ];
    for (let i = 0; i < specs.length; i++) {
      list.appendChild(buildSampleCard(specs[i]));
    }
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
      if (state.convStash !== null) {
        list.innerHTML = state.convStash;
      }
    }
    state.convStash = null;
    state.samplesInjected = false;
  }

  // ---- Positioning ----
  function positionCard(card, rect) {
    // Mobile bottom sheet: CSS handles layout; do not set top/left.
    if (window.innerWidth <= 520) {
      card.style.top = '';
      card.style.left = '';
      return;
    }

    const margin = 12;
    const gap = 12;
    // Measure current size.
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
      // below
      top = rect.bottom + gap;
      left = rect.left + rect.width / 2 - cw / 2;
    } else if (spaceAbove >= ch + gap) {
      // above
      top = rect.top - gap - ch;
      left = rect.left + rect.width / 2 - cw / 2;
    } else if (spaceRight >= cw + gap) {
      // right
      left = rect.right + gap;
      top = rect.top + rect.height / 2 - ch / 2;
    } else if (spaceLeft >= cw + gap) {
      // left
      left = rect.left - gap - cw;
      top = rect.top + rect.height / 2 - ch / 2;
    } else {
      // fallback: below, clamped
      top = rect.bottom + gap;
      left = rect.left;
    }

    // Clamp within viewport.
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

  // ---- Rendering ----
  function currentSteps() {
    return PATHS[state.path] ? PATHS[state.path].steps : [];
  }

  function renderDots(container, activeIndex) {
    // 7 dots: 6 path steps + finale. activeIndex 0..6.
    const wrap = makeEl('div', 'fft-dots');
    for (let i = 0; i < 7; i++) {
      const d = makeEl('span', 'fft-dot');
      if (i < activeIndex) d.classList.add('fft-done');
      else if (i === activeIndex) d.classList.add('fft-cur');
      wrap.appendChild(d);
    }
    container.appendChild(wrap);
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
    b.textContent = 'Skip tour';
    b.addEventListener('click', function () {
      end('skip');
    });
    return b;
  }

  // ---- Centered cards ----
  function showWelcome() {
    clearNodes();
    state.currentAnchor = null;

    const backdrop = track(makeEl('div', 'fft-backdrop'));
    document.body.appendChild(backdrop);

    const card = track(makeEl('div', 'fft-center-card'));

    const eyebrow = makeEl('div', 'fft-eyebrow');
    eyebrow.textContent = WELCOME.eyebrow;
    card.appendChild(eyebrow);

    const title = makeEl('div', 'fft-title');
    title.textContent = WELCOME.title;
    card.appendChild(title);

    const body = makeEl('p', 'fft-body');
    body.textContent = WELCOME.body;
    card.appendChild(body);

    const row = makeEl('div', 'fft-btnrow');
    const spacer = makeEl('div', null);
    spacer.style.marginRight = 'auto';
    row.appendChild(spacer);
    row.appendChild(
      makeButton(WELCOME.ghost, 'fft-btn-ghost', function () {
        end('skip');
      })
    );
    row.appendChild(
      makeButton(WELCOME.primary, 'fft-btn-primary', function () {
        showFork();
      })
    );
    card.appendChild(row);

    document.body.appendChild(card);
  }

  function showFork() {
    clearNodes();
    state.currentAnchor = null;

    const backdrop = track(makeEl('div', 'fft-backdrop'));
    document.body.appendChild(backdrop);

    const card = track(makeEl('div', 'fft-center-card'));
    card.appendChild(makeSkipLink());

    const title = makeEl('div', 'fft-title');
    title.textContent = FORK.title;
    card.appendChild(title);

    const body = makeEl('p', 'fft-body');
    body.textContent = FORK.body;
    card.appendChild(body);

    const stack = makeEl('div', 'fft-btnrow fft-stack');
    for (let i = 0; i < FORK.choices.length; i++) {
      const choice = FORK.choices[i];
      const btn = makeEl('button', 'fft-choice');
      btn.type = 'button';
      const ct = makeEl('div', 'fft-choice-title');
      ct.textContent = choice.label;
      btn.appendChild(ct);
      const cs = makeEl('div', 'fft-choice-sub');
      cs.textContent = choice.sub;
      btn.appendChild(cs);
      btn.addEventListener(
        'click',
        (function (p) {
          return function () {
            startPath(p);
          };
        })(choice.path)
      );
      stack.appendChild(btn);
    }
    card.appendChild(stack);

    document.body.appendChild(card);
  }

  function startPath(path) {
    state.path = path;
    state.stepIndex = 0;
    showStep(0, 1);
  }

  // ---- Spotlight steps ----
  // anchor: selector string or array of selectors; first visible match wins.
  function resolveAnchor(anchor) {
    const sels = Array.isArray(anchor) ? anchor : [anchor];
    for (let i = 0; i < sels.length; i++) {
      const el = document.querySelector(sels[i]);
      if (!el) continue;
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) return el;
    }
    return null;
  }

  // direction: +1 forward, -1 backward. Used to skip missing anchors.
  function showStep(index, direction) {
    const steps = currentSteps();

    // Bounds -> finale (forward) or fork/welcome (backward).
    if (index >= steps.length) {
      showFinale();
      return;
    }
    if (index < 0) {
      showFork();
      return;
    }

    const step = steps[index];

    // Prepare sample rows if this step needs them.
    if (step.needsRows) {
      if (!document.querySelector('#convList .conv-item')) {
        try {
          injectSamples();
        } catch (_) {
          /* ignore */
        }
      }
    }

    const el = resolveAnchor(step.anchor);
    let rect = null;
    if (el) {
      try {
        el.scrollIntoView({ block: 'center', behavior: 'instant' });
      } catch (_) {
        try {
          el.scrollIntoView();
        } catch (__) {
          /* ignore */
        }
      }
      rect = el.getBoundingClientRect();
    }

    const missing = !el || !rect || rect.width === 0 || rect.height === 0;
    if (missing) {
      // Skip in direction of travel.
      const next = index + (direction >= 0 ? 1 : -1);
      showStep(next, direction);
      return;
    }

    state.stepIndex = index;
    state.currentAnchor = el;
    clearNodes();

    // Shield blocks app pointer events.
    const shield = track(makeEl('div', 'fft-shield'));
    document.body.appendChild(shield);

    // Spot cutout.
    const spot = track(makeEl('div', 'fft-spot'));
    document.body.appendChild(spot);
    positionSpot(spot, rect);

    // Card.
    const card = track(makeEl('div', 'fft-card fft-anim'));
    card.appendChild(makeSkipLink());

    const title = makeEl('div', 'fft-title');
    title.textContent = step.title;
    card.appendChild(title);

    const body = makeEl('p', 'fft-body');
    body.textContent = step.body;
    card.appendChild(body);

    // Sample note as a second paragraph, only when samples were injected.
    if (step.needsRows && step.sampleNote && state.samplesInjected) {
      const note = makeEl('p', 'fft-body');
      note.textContent = step.sampleNote;
      card.appendChild(note);
    }

    // Footer.
    const row = makeEl('div', 'fft-btnrow');
    renderDots(row, index);

    if (index > 0) {
      row.appendChild(
        makeButton('Back', 'fft-btn-ghost', function () {
          showStep(state.stepIndex - 1, -1);
        })
      );
    }
    row.appendChild(
      makeButton('Next', 'fft-btn-primary', function () {
        showStep(state.stepIndex + 1, 1);
      })
    );
    card.appendChild(row);

    document.body.appendChild(card);
    positionCard(card, rect);
  }

  function showFinale() {
    clearNodes();
    state.currentAnchor = null;

    const finale = PATHS[state.path] ? PATHS[state.path].finale : null;
    if (!finale) {
      end('done');
      return;
    }

    const backdrop = track(makeEl('div', 'fft-backdrop'));
    document.body.appendChild(backdrop);

    const card = track(makeEl('div', 'fft-center-card'));
    card.appendChild(makeSkipLink());

    const title = makeEl('div', 'fft-title');
    title.textContent = finale.title;
    card.appendChild(title);

    const body = makeEl('p', 'fft-body');
    body.textContent = finale.body;
    card.appendChild(body);

    const ol = makeEl('ol', 'fft-list');
    for (let i = 0; i < finale.list.length; i++) {
      const li = makeEl('li', null);
      li.textContent = finale.list[i];
      ol.appendChild(li);
    }
    card.appendChild(ol);

    const row = makeEl('div', 'fft-btnrow');
    renderDots(row, 6);
    row.appendChild(
      makeButton(finale.button, 'fft-btn-primary', function () {
        end('done');
      })
    );
    card.appendChild(row);

    document.body.appendChild(card);
  }

  // ---- Keyboard ----
  function onKeyDown(e) {
    if (!state.active) return;

    const key = e.key;
    const isFork = !state.path && document.querySelector('.fft-choice');
    const isWelcome =
      !state.path && !isFork && document.querySelector('.fft-center-card');

    if (key === 'Escape') {
      e.stopPropagation();
      e.preventDefault();
      end('skip');
      return;
    }

    if (isFork) {
      if (key === '1') {
        e.stopPropagation();
        e.preventDefault();
        startPath('newcomer');
        return;
      }
      if (key === '2') {
        e.stopPropagation();
        e.preventDefault();
        startPath('multi');
        return;
      }
      if (key === 'ArrowLeft') {
        e.stopPropagation();
        e.preventDefault();
        showWelcome();
        return;
      }
      // Other keys on fork: swallow to keep app quiet.
      e.stopPropagation();
      return;
    }

    if (isWelcome) {
      if (key === 'ArrowRight' || key === 'Enter') {
        e.stopPropagation();
        e.preventDefault();
        showFork();
        return;
      }
      e.stopPropagation();
      return;
    }

    // Path steps or finale.
    if (key === 'ArrowRight' || key === 'Enter') {
      e.stopPropagation();
      e.preventDefault();
      if (state.path && document.querySelector('.fft-card')) {
        showStep(state.stepIndex + 1, 1);
      } else {
        // Finale primary.
        end('done');
      }
      return;
    }
    if (key === 'ArrowLeft') {
      e.stopPropagation();
      e.preventDefault();
      if (state.path && document.querySelector('.fft-card')) {
        showStep(state.stepIndex - 1, -1);
      } else if (state.path) {
        // Finale: step back to the last spotlight step.
        showStep(currentSteps().length - 1, -1);
      }
      return;
    }
    // Swallow other keys while active so app shortcuts stay out.
    e.stopPropagation();
  }

  // ---- Public API ----
  function start(opts) {
    const options = opts || {};
    if (state.active) return;

    const done = lsGet(DONE_KEY);
    if (done && !options.force) return;

    ensureStyle();

    state.active = true;
    state.path = null;
    state.stepIndex = 0;
    state.convStash = null;
    state.samplesInjected = false;
    state.currentAnchor = null;
    window.__cccTourActive = true;

    // Keyboard listener (capture phase).
    state.keyHandler = onKeyDown;
    document.addEventListener('keydown', state.keyHandler, true);

    // Reposition listeners.
    state.repoHandler = onRepoEvent;
    window.addEventListener('resize', state.repoHandler, { passive: true });
    window.addEventListener('scroll', state.repoHandler, {
      capture: true,
      passive: true
    });

    try {
      showWelcome();
    } catch (_) {
      end('error');
    }
  }

  function end(reason) {
    if (!state.active) {
      // Still make sure any stray flag is cleared.
      window.__cccTourActive = false;
      return;
    }
    state.active = false;

    // Remove listeners.
    if (state.keyHandler) {
      document.removeEventListener('keydown', state.keyHandler, true);
      state.keyHandler = null;
    }
    if (state.repoHandler) {
      window.removeEventListener('resize', state.repoHandler, { passive: true });
      window.removeEventListener('scroll', state.repoHandler, {
        capture: true,
        passive: true
      });
      state.repoHandler = null;
    }

    // Remove DOM.
    clearNodes();

    // Restore samples/stash.
    try {
      restoreSamples();
    } catch (_) {
      /* ignore */
    }

    window.__cccTourActive = false;

    // Persist flag.
    let flag;
    if (reason === 'skip' || reason === 'error') {
      flag = 'skipped';
    } else {
      flag = state.path || 'skipped';
    }
    lsSet(DONE_KEY, flag);

    state.path = null;
    state.stepIndex = 0;
    state.currentAnchor = null;
  }

  window.cccTour = { start: start, end: end };
})();
