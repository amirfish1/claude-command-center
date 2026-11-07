// The demo ribbon: says plainly that this is invented, shows the live counts,
// and points at the next thing to try. Everything else on the page is the
// real CCC interface.
(function () {
  'use strict';
  const INSTALL_URL = 'https://github.com/amirfish1/claude-command-center#quickstart';
  const demo = window.__tryDemo;
  const ribbon = document.createElement('div');
  ribbon.id = 'tryDemoRibbon';
  ribbon.innerHTML = '<span class="tag">DEMO</span><span class="note">Invented data, nothing leaves your browser.</span><span class="hint"></span>'
    + '<span class="stats"></span><a target="_blank" rel="noopener" href="' + INSTALL_URL + '">Install CCC</a>';
  document.body.appendChild(ribbon);
  const toast = document.createElement('div');
  toast.id = 'tryDemoToast';
  toast.hidden = true;
  document.body.appendChild(toast);
  const hint = ribbon.querySelector('.hint');
  const stats = ribbon.querySelector('.stats');

  // The 60-second path: three things to try, then the Install CCC step. Steps
  // reached are counted in this browser only (localStorage). Nothing is sent
  // anywhere. A future, owner-approved analytics hook can subscribe by setting
  // window.__tryDemoAnalyticsHook = function (step, count) {...}; until then
  // the counter stays local.
  const STEPS = [
    { key: 'decision', hint: 'Step 1 of 4: tap a decision under any row. Try "Round on total".' },
    { key: 'approval', hint: 'Step 2 of 4: open "Rotate the staging database password" and tap Approve.' },
    { key: 'reply', hint: 'Step 3 of 4: type a reply to an agent in the box at the bottom, then press Enter.' },
    { key: 'install', hint: 'Step 4 of 4: that is the whole loop. Install CCC to run it on your own agents.' },
  ];
  const COUNTER_KEY = 'ccc-try-demo-steps';
  let reached = {};
  try { reached = JSON.parse(localStorage.getItem(COUNTER_KEY) || '{}') || {}; } catch (_) { reached = {}; }
  function recordStep(key) {
    if (reached[key]) return;
    reached[key] = Date.now();
    try { localStorage.setItem(COUNTER_KEY, JSON.stringify(reached)); } catch (_) {}
    const count = Object.keys(reached).length;
    window.__tryDemoSteps = { reached: Object.assign({}, reached), count };
    if (typeof window.__tryDemoAnalyticsHook === 'function') {
      try { window.__tryDemoAnalyticsHook(key, count); } catch (_) {}
    }
    paint();
  }
  recordStep('loaded');
  window.addEventListener('tryDemo:step', ev => recordStep(ev.detail));
  ribbon.querySelector('a').addEventListener('click', () => recordStep('install'));

  const HINTS = {
    cleared: 'No decisions left. New ones arrive as the working agents finish. Install CCC to run your own.',
    done: 'That is the whole loop. More agents still need you here, or Install CCC to run your own.',
  };
  function nextStep() { return STEPS.find(s => !reached[s.key]); }
  function paint() {
    const c = demo.counts();
    // One source for "need you": the rows the sidebar shows with the needs-you
    // dot, which is also what the footer pill counts.
    const needs = document.querySelectorAll('.conv-item.is-needs-you').length;
    demo.shownNeeds = needs;
    stats.innerHTML = '<b>' + c.total + '</b> agents · <b>' + c.working + '</b> working · '
      + '<span class="needs"><b>' + needs + '</b> need you</span>';
    const next = nextStep();
    hint.textContent = next ? next.hint : (needs ? HINTS.done : HINTS.cleared);
    ribbon.classList.toggle('at-install', !!next && next.key === 'install');
  }
  paint();
  setInterval(paint, 500);

  let toastTimer = 0;
  window.__tryDemoToast = function (text) {
    toast.textContent = text;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.hidden = true; }, 4200);
  };

  // Menu entries that open machine pages: say so in one line instead of a
  // blank pane or an error. Matched on the visible label.
  const NEEDS_MACHINE = /^(terminal|history|worktrees|stats & usage|today|notes|fleet overview|fleet pulse|voice setup|hands-free operator setup|search history|new group chat|group chat live view|system status|report a problem|check for updates|what's new|discord community)/i;
  document.addEventListener('click', ev => {
    const item = ev.target.closest && ev.target.closest('button, a, [role=menuitem], .menu-item');
    if (!item || item.closest('#convList') || item.closest('#tryDemoRibbon')) return;
    const label = (item.innerText || '').replace(/^[^A-Za-z]+/, '').trim();
    if (!NEEDS_MACHINE.test(label) || !item.closest('[role=menu], .sidebar-more-menu, [class*=menu]')) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    window.__tryDemoToast('That needs a real machine behind it. Install CCC to do this with your own agents.');
  }, true);

  // Open the top decision so the transcript is there on first look. Phones
  // stay on the list, where the chips are.
  const wide = window.matchMedia('(min-width: 900px)').matches;
  const firstDecision = demo.sessions.find(r => r.spec.kind === 'decide');
  if (wide && firstDecision) {
    const started = Date.now();
    const opener = setInterval(() => {
      const row = document.querySelector('#convList .conv-item[data-session-id="' + firstDecision.s.session_id + '"]');
      if (row) { clearInterval(opener); row.click(); }
      else if (Date.now() - started > 15000) clearInterval(opener);
    }, 150);
  }
})();
