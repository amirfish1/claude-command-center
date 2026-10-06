/* Star ask (Q17): a small "star us on GitHub" card shown at success moments.
 *
 * Server-side rules (ccc_server/star_ask.py, /api/star) do the policing:
 * max one ask per 14 days, max 3 asks ever, "Don't ask again" is permanent,
 * and a confirmed star (ours or a manual one on github.com) ends all asks.
 * This file only decides WHEN a moment counts and renders the card.
 *
 * How other surfaces report a success moment (the contract):
 *   document.dispatchEvent(new CustomEvent('ccc:success-moment',
 *     { detail: { kind: 'first_task' } }))     // e.g. L09 first task, L13 milestone
 *   window.cccStarAsk.moment('pr_merged')      // same thing, direct call
 *
 * Built-in moments in this file:
 *   - 'task_done'  : app.js fires ccc:success-moment when a live session wraps up
 *   - 'pr_merged'  : a new ".pr-merged" chip appears in the session lists
 *
 * If the GitHub CLI is signed in, the primary button stars the repo in one
 * click (no browser needed). Otherwise it opens github.com so the user can
 * tap the star there. No rewards, no nagging: the card is never modal.
 */
(function () {
  'use strict';
  if (window.cccStarAsk) return;

  var ENDPOINT = '/api/star';
  var SEEN_PRS_KEY = 'ccc-star-ask-seen-prs';
  var MOMENT_DEBOUNCE_MS = 60 * 1000;  // bursts of moments collapse to one card
  var PR_SCAN_MS = 20 * 1000;

  var _card = null;
  var _lastTry = 0;
  var _prBaselineDone = false;

  function reducedMotion() {
    if (window.cccFx && typeof window.cccFx.reducedMotion === 'function') {
      try { return !!window.cccFx.reducedMotion(); } catch (_) { /* fall through */ }
    }
    try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; }
    catch (_) { return false; }
  }

  function muted() {
    if (window.cccFx && typeof window.cccFx.muted === 'function') {
      try { return !!window.cccFx.muted(); } catch (_) { /* fall through */ }
    }
    try { return localStorage.getItem('ccc-sounds-enabled') === '0'; }
    catch (_) { return false; }
  }

  function celebrate() {
    var fx = window.cccFx;
    if (fx) {
      try { if (!reducedMotion() && typeof fx.confetti === 'function') fx.confetti({ particleCount: 70 }); } catch (_) {}
      try { if (!muted() && typeof fx.play === 'function') fx.play('coin'); } catch (_) {}
      return;
    }
    // fx kit (L08) not merged yet: reuse the built-in done chime.
    try {
      var s = window.cccSounds;
      if (s && !muted() && typeof s.playDone === 'function') {
        if (typeof s.enabled !== 'function' || s.enabled()) s.playDone();
      }
    } catch (_) {}
  }

  function post(action) {
    return fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: action }),
    }).then(function (r) { return r.json(); }).catch(function () { return {}; });
  }

  function dismiss(mode) {
    var card = _card;
    _card = null;
    document.removeEventListener('keydown', onKey, true);
    if (card) {
      card.classList.add('is-leaving');
      setTimeout(function () { card.remove(); }, reducedMotion() ? 0 : 200);
    }
    if (mode === 'never' || mode === 'later') post(mode);
  }

  function onKey(e) {
    if (e.key === 'Escape' && _card) {
      e.stopPropagation();
      dismiss('later');
    }
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function showThanks(card) {
    card.classList.add('is-thanked');
    card.innerHTML = '';
    var head = el('div', 'ccc-star-ask__head');
    head.appendChild(el('span', 'ccc-star-ask__star', '★'));
    head.appendChild(el('div', 'ccc-star-ask__title', 'You are a star.'));
    card.appendChild(head);
    card.appendChild(el('p', 'ccc-star-ask__body',
      'Thanks for helping people find a free AI dev team.'));
    celebrate();
    setTimeout(function () { if (_card === card) dismiss(); }, 4200);
  }

  function showFallback(card, repoUrl, note) {
    card.innerHTML = '';
    var head = el('div', 'ccc-star-ask__head');
    head.appendChild(el('span', 'ccc-star-ask__star', '★'));
    head.appendChild(el('div', 'ccc-star-ask__title', 'Almost there'));
    card.appendChild(head);
    card.appendChild(el('p', 'ccc-star-ask__body',
      note || 'One more step: open the page and tap the star.'));
    var actions = el('div', 'ccc-star-ask__actions');
    var open = el('a', 'ccc-star-ask__btn ccc-star-ask__btn--primary', 'Open GitHub ★');
    open.href = repoUrl;
    open.target = '_blank';
    open.rel = 'noopener noreferrer';
    var later = el('button', 'ccc-star-ask__btn ccc-star-ask__btn--text', 'Close');
    later.type = 'button';
    later.addEventListener('click', function () { dismiss('later'); });
    actions.appendChild(open);
    actions.appendChild(later);
    card.appendChild(actions);
    open.focus();
  }

  function render(status, kind) {
    if (_card) return;
    var repoUrl = status.repo_url || 'https://github.com/amirfish1/claude-command-center';
    var card = el('div', 'ccc-star-ask');
    if (reducedMotion()) card.classList.add('ccc-star-ask--still');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-label', 'Star Command Center on GitHub');

    var close = el('button', 'ccc-star-ask__close', '×');
    close.type = 'button';
    close.title = 'Maybe later';
    close.setAttribute('aria-label', 'Dismiss for now');
    close.addEventListener('click', function () { dismiss('later'); });
    card.appendChild(close);

    var head = el('div', 'ccc-star-ask__head');
    head.appendChild(el('span', 'ccc-star-ask__star', '★'));
    head.appendChild(el('div', 'ccc-star-ask__title', 'Enjoying Command Center?'));
    card.appendChild(head);

    card.appendChild(el('p', 'ccc-star-ask__body',
      kind === 'first_task'
        ? 'You just ran your first task for $0. One little star on GitHub helps other people find this too.'
        : 'One little star on GitHub helps other people find their own free AI dev team.'));

    var actions = el('div', 'ccc-star-ask__actions');

    if (status.gh_available) {
      var starBtn = el('button', 'ccc-star-ask__btn ccc-star-ask__btn--primary', '★ Star it');
      starBtn.type = 'button';
      starBtn.addEventListener('click', function () {
        starBtn.disabled = true;
        starBtn.textContent = 'Starring…';
        post('star').then(function (res) {
          if (res && res.ok) { showThanks(card); return; }
          showFallback(card, repoUrl,
            res && res.code === 'gh_auth'
              ? 'GitHub CLI is not signed in here. No problem, the browser works too.'
              : 'The quick way did not work here. The browser works too.');
        });
      });
      actions.appendChild(starBtn);
    } else {
      var open = el('a', 'ccc-star-ask__btn ccc-star-ask__btn--primary', 'Star on GitHub ★');
      open.href = repoUrl;
      open.target = '_blank';
      open.rel = 'noopener noreferrer';
      open.addEventListener('click', function () {
        // No "never" here: the daily remote check in /api/star picks up a
        // manual star on its own, and a click-through that did not star
        // should not burn the remaining asks.
        dismiss();
      });
      actions.appendChild(open);
      card.appendChild(el('p', 'ccc-star-ask__note', 'Opens github.com. Tap the star up top.'));
    }

    var later = el('button', 'ccc-star-ask__btn', 'Maybe later');
    later.type = 'button';
    later.addEventListener('click', function () { dismiss('later'); });
    actions.appendChild(later);

    var never = el('button', 'ccc-star-ask__btn ccc-star-ask__btn--text', 'Don\'t ask again');
    never.type = 'button';
    never.addEventListener('click', function () { dismiss('never'); });
    actions.appendChild(never);

    card.appendChild(actions);

    document.body.appendChild(card);
    _card = card;
    document.addEventListener('keydown', onKey, true);
    var primary = card.querySelector('.ccc-star-ask__btn--primary');
    if (primary) primary.focus();
  }

  /* A success moment happened. Check the server-side ledger (cheap: one JSON
     read; the remote star check is TTL'd to once a day) and maybe show. */
  function moment(kind) {
    if (_card) return;
    var now = Date.now();
    if (now - _lastTry < MOMENT_DEBOUNCE_MS) return;
    _lastTry = now;
    fetch(ENDPOINT, { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (st) {
        if (!st || !st.ok || !st.should_ask) return;
        render(st, kind);
        // The ask is counted when the card is actually on screen.
        post('shown');
      })
      .catch(function () {});
  }

  /* PR-merged moments without touching app.js: every 20s collect the
     ".pr-merged" chips' PR numbers. The first scan only sets the baseline
     (PRs merged long ago are not a moment); a NEW merged number is. */
  function seenPrs() {
    try { return new Set(JSON.parse(localStorage.getItem(SEEN_PRS_KEY) || '[]')); }
    catch (_) { return new Set(); }
  }

  function scanMergedPrs() {
    var found = new Set();
    var chips = document.querySelectorAll('.pr-merged');
    for (var i = 0; i < chips.length; i++) {
      var m = /PR\s*#(\d+)/i.exec(chips[i].textContent || '');
      if (m) found.add(m[1]);
    }
    var seen = seenPrs();
    var fresh = [];
    found.forEach(function (n) { if (!seen.has(n)) fresh.push(n); });
    found.forEach(function (n) { seen.add(n); });
    if (seen.size > 200) seen = new Set(Array.from(seen).slice(-200));
    try { localStorage.setItem(SEEN_PRS_KEY, JSON.stringify(Array.from(seen))); } catch (_) {}
    if (!_prBaselineDone) { _prBaselineDone = true; return; }
    if (fresh.length) moment('pr_merged');
  }

  document.addEventListener('ccc:success-moment', function (e) {
    moment(e && e.detail && e.detail.kind);
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(scanMergedPrs, 3000); });
  } else {
    setTimeout(scanMergedPrs, 3000);
  }
  setInterval(scanMergedPrs, PR_SCAN_MS);

  window.cccStarAsk = {
    moment: moment,
    /* Dev/design preview: render the card as it would look with gh signed
       in. Does not touch the ledger, so it never spends an ask. */
    preview: function () {
      render({
        repo_url: 'https://github.com/amirfish1/claude-command-center',
        gh_available: true,
      }, 'preview');
    },
    dismiss: dismiss,
  };
})();
