// The demo ribbon: says plainly that this is invented, shows the live counts,
// and points at the next thing to try. Everything else on the page is the
// real CCC interface.
(function () {
  'use strict';
  const INSTALL_URL = 'https://github.com/amirfish1/claude-command-center#quickstart';
  const demo = window.__tryDemo;
  const ribbon = document.createElement('div');
  ribbon.id = 'tryDemoRibbon';
  ribbon.innerHTML = '<span class="tag">DEMO</span><span class="hint"></span>'
    + '<span class="stats"></span><a target="_blank" rel="noopener" href="' + INSTALL_URL + '">Install CCC</a>';
  document.body.appendChild(ribbon);
  const toast = document.createElement('div');
  toast.id = 'tryDemoToast';
  toast.hidden = true;
  document.body.appendChild(toast);
  const hint = ribbon.querySelector('.hint');
  const stats = ribbon.querySelector('.stats');

  const HINTS = {
    start: 'Invented sessions for a made-up tea shop. No agent runs and nothing leaves your browser. Tap a decision under any row.',
    tapped: 'That agent went back to work. Its row updates when it finishes. Try the next decision.',
    cleared: 'No decisions left. New ones arrive as the working agents finish.',
  };
  function paint() {
    const c = demo.counts();
    stats.innerHTML = '<b>' + c.total + '</b> agents · <b>' + c.working + '</b> working · '
      + '<span class="needs"><b>' + c.needsYou + '</b> need you</span>';
    hint.textContent = !demo.taps ? HINTS.start : c.needsYou ? HINTS.tapped : HINTS.cleared;
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
