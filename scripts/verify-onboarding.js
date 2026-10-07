// verify-onboarding.js — drive /?onboarding=1 in headless Chrome and prove the
// Moment Zero flow actually renders and advances for a first-run user.
//
// Companion to scripts/e2e-novice.sh; safe to run standalone:
//   CCC_E2E_CCC_URL=http://127.0.0.1:9018 node scripts/verify-onboarding.js
//
// Exit codes:
//   0  onboarding shell found and walked to an end state (or all CTAs done)
//   3  onboarding feature absent — distinct from a broken run so callers can
//      degrade gracefully
//   1  real failure (server unreachable, page error, stuck walk)
//
// Env:
//   CCC_E2E_CCC_URL     base URL of the CCC server (default http://127.0.0.1:9018)
//   CCC_E2E_SHOTS       screenshot dir (default $TMPDIR/ccc-onboarding-<pid>)
//   CCC_E2E_TIMEOUT_MS  overall deadline (default 120000)

const fs = require('fs');
const os = require('os');
const path = require('path');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const BASE = (process.env.CCC_E2E_CCC_URL || 'http://127.0.0.1:9018').replace(/\/$/, '');
const SHOTS = process.env.CCC_E2E_SHOTS
  || path.join(os.tmpdir(), `ccc-onboarding-${process.pid}`);
const TIMEOUT_MS = Number(process.env.CCC_E2E_TIMEOUT_MS) || 180_000;
// Generous: real setup steps + a first agent task can take a while, and the
// loop exits early on finale/idle anyway — the timeout is the real bound.
const MAX_STEPS = Math.max(60, Math.ceil(TIMEOUT_MS / 700));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// The Moment Zero shell's real contract (static/onboarding/onboarding.js):
// root #cccMomentZero with .mz-overlay. Generic markers stay as fallbacks in
// case the shell's markup moves. Deliberately NOT [id*="onboard"]: the
// pre-existing first-flight modal (#onboardingModal, .onb-*) is not the thing
// being verified and must not false-positive here.
const ONBOARDING_SCOPE = [
  '#cccMomentZero',
  '.mz-overlay',
  '[data-onboarding]',
  '.onboarding-shell',
].join(', ');

function shotPath(name) {
  fs.mkdirSync(SHOTS, { recursive: true });
  return path.join(SHOTS, name);
}

// Click the first visible button whose text matches a pattern. When scopeSel
// is given, only buttons inside that container are eligible — the walk must
// never reach arbitrary dashboard buttons (e.g. engine "Install" in the
// legacy first-flight modal) on a server with real data.
//
// Two passes: primary CTAs always win; deferral buttons (skip/later/explore)
// are only used when no primary CTA exists, so the walk takes the "happy
// path" through steps instead of bailing early.
// Returns the clicked text, or ''. Runs in the page so Playwright-style
// locator APIs (absent in puppeteer) are never needed.
const DEFERRAL_RE = /skip|later|explore|not now|dismiss|my own/i;

// Click the first visible element matching a CSS selector inside the scope —
// for card-style CTAs (the task picker) that carry no standard button label.
function clickSelector(page, sel, scopeSel) {
  return page.evaluate((s, scope) => {
    const root = scope ? document.querySelector(scope) : document;
    if (!root) return '';
    const el = root.querySelector(s);
    if (!el) return '';
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return '';
    el.click();
    return (el.innerText || s).trim().slice(0, 60);
  }, sel, scopeSel || null);
}

function clickButtonMatching(page, pattern, scopeSel, allowDeferred) {
  return page.evaluate((reSrc, scope, deferSrc, allowDef) => {
    const re = new RegExp(reSrc, 'i');
    const defer = new RegExp(deferSrc, 'i');
    const root = scope ? document.querySelector(scope) : document;
    if (!root) return '';
    const els = Array.from(
      root.querySelectorAll('button, [role="button"], a.btn, input[type="button"]')
    );
    let deferredEl = null;
    let deferredLabel = '';
    for (const el of els) {
      const label = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
      if (!label || !re.test(label)) continue;
      // The top-chrome "Skip setup" aborts the whole flow ("set up" matches
      // the CTA pattern) — a verifier never wants the abort button.
      if (el.closest && el.closest('.mz-skip')) continue;
      const r = el.getBoundingClientRect();
      const visible = r.width > 0 && r.height > 0
        && getComputedStyle(el).visibility !== 'hidden';
      if (!visible) continue;
      if (defer.test(label)) {
        if (!deferredEl) { deferredEl = el; deferredLabel = label; }
        continue;
      }
      el.click();
      return label;
    }
    if (allowDef && deferredEl) {
      deferredEl.click();
      return deferredLabel;
    }
    return '';
  }, pattern.source, scopeSel || null, DEFERRAL_RE.source, !!allowDeferred);
}

function onboardingPresence(page) {
  return page.evaluate((scope) => {
    // window.cccOnboarding exists whenever onboarding.js loaded — presence is
    // the shell actually being OPEN, not the module being imported.
    const api = window.cccOnboarding;
    const byGlobal = !!(api && typeof api.isOpen === 'function' && api.isOpen());
    const byDom = !!document.querySelector(scope);
    // Recorded as evidence only — never proof. Body innerText includes live
    // session titles on a server with real history, which can contain phrases
    // like "your own AI dev team" and produced a false positive that sent the
    // walk clicking legacy first-flight Install buttons.
    const dlgText = Array.from(
      document.querySelectorAll(`${scope}, [role="dialog"]`)
    ).map((el) => (el.innerText || '').toLowerCase()).join(' ');
    const byText = /your own ai dev team|ai dev team\.?\s*free|moment zero/.test(dlgText);
    return { byGlobal, byDom, byText, found: byGlobal || byDom };
  }, ONBOARDING_SCOPE);
}

// A snapshot of the DOM we can diff between steps to know the click did
// something. Cheap structural fingerprint, not a full serialisation.
function domFingerprint(page, scopeSel) {
  return page.evaluate((scope) => {
    const root = scope ? document.querySelector(scope) : document;
    if (!root) return '';
    const els = root.querySelectorAll('h1,h2,h3,button,[data-step],[role="dialog"]');
    return Array.from(els)
      .map((el) => `${el.tagName}:${(el.innerText || '').slice(0, 60)}`)
      .join('|');
  }, scopeSel || null);
}

async function dismissChromeOverlays(page) {
  // First-load modals that sit above the app: telemetry consent, the FIRST
  // FLIGHT tour, the install toast. Match on button text so this keeps working
  // if their markup shifts.
  // Dismissals only — no /continue|next|set up|install/ here. Those advance
  // real flows (legacy first-flight, Tailscale setup) rather than close them.
  const dismisses = [
    /skip for now/i,
    /^skip$/i,
    /got it/i,
    /not now/i,
    /maybe later/i,
    /^later$/i,
    /dismiss/i,
  ];
  for (const re of dismisses) {
    const clicked = await clickButtonMatching(page, re, null, true);
    if (clicked) {
      console.log(`[verify-onboarding] dismissed overlay via "${clicked}"`);
      await sleep(400);
    }
  }
}

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const result = {
    url: `${BASE}/?onboarding=1`,
    shots: SHOTS,
    steps: [],
    reached_end: false,
    first_task_done: false,
    page_errors: [],
    onboarding_present: false,
  };

  const chromePath = findChromePath();
  const browser = await puppeteer.launch({
    executablePath: chromePath,
    args: ['--no-sandbox'],
  });

  let deadline;
  try {
    const timedOut = new Promise((_, reject) => {
      deadline = setTimeout(
        () => reject(new Error(`verify-onboarding exceeded ${TIMEOUT_MS}ms`)),
        TIMEOUT_MS
      );
    });

    await Promise.race([timedOut, (async () => {
      const page = await browser.newPage();
      await page.setViewport({ width: 1280, height: 800 });
      page.on('pageerror', (e) => {
        result.page_errors.push(e.message);
        console.log(`[verify-onboarding] page error: ${e.message}`);
      });

      await page.goto(`${BASE}/?onboarding=1`, {
        waitUntil: 'load',
        timeout: 30_000,
      });
      // CCC polls forever — 'load' + a short settle beats networkidle*.
      await page.waitForNetworkIdle({ idleTime: 750, timeout: 5000 }).catch(() => {});

      await dismissChromeOverlays(page);
      await sleep(600);

      const presence = await onboardingPresence(page);
      result.onboarding_present = presence.found;
      result.presence = presence;
      await page.screenshot({ path: shotPath('00-loaded.png') });

      if (!presence.found) {
        console.log(
          '[verify-onboarding] onboarding shell not present on /?onboarding=1 '
          + '(graceful absence — the feature may not be in this build)'
        );
        console.log(`ONBOARDING_RESULT ${JSON.stringify(result)}`);
        process.exitCode = 3;
        return;
      }

      console.log('[verify-onboarding] onboarding shell detected, walking steps');
      let lastFp = await domFingerprint(page, ONBOARDING_SCOPE);
      let idleIters = 0;
      for (let step = 0; step < MAX_STEPS; step++) {
        await page.screenshot({
          path: shotPath(`step-${String(step).padStart(2, '0')}.png`),
        });
        const state = await page.evaluate((scope) => {
          const root = document.querySelector(scope);
          if (!root) return { gone: true };
          const body = (root.innerText || '').toLowerCase();
          // Setup steps and the first task run themselves — a busy screen must
          // be waited through, not treated as a dead end.
          const busy = !!root.querySelector(
            '.mz-taskrun, .mz-progress, .mz-step-state.is-run, .is-run, '
            + '.mz-step-running, .mz-spinner, .mz-loading, [aria-busy="true"]'
          );
          // The finale scene has its own classes; "cost $0" requires the word
          // "cost" so the welcome line "runs them on $0 models" can't match.
          const finale = !!root.querySelector('.mz-finale, .mz-big-cost')
            || /cost \$0|setup complete|all set|you'?re (all )?done|done!|celebrat/.test(body);
          return { gone: false, busy, finale, text: body.slice(0, 300) };
        }, ONBOARDING_SCOPE);
        if (state.gone) {
          console.log(`[verify-onboarding] shell closed at step ${step} — walk ends here`);
          break;
        }
        result.steps.push({ step, finale: state.finale, busy: state.busy });
        if (state.finale) {
          result.reached_end = true;
          result.first_task_done = await page.evaluate(() => !!(window.cccOnboarding && window.cccOnboarding._state && window.cccOnboarding._state.firstTaskDone));
          console.log(`[verify-onboarding] reached the finale at step ${step}`);
          // Let the finale card paint before the closing screenshot.
          await sleep(900);
          break;
        }

        let clicked = '';
        if (!state.busy) {
          // Happy path first: real CTA, then the task picker cards, and only
          // then deferral buttons (skip/later) so steps aren't skipped early.
          clicked = await clickButtonMatching(
            page,
            /continue|next|start|install|let'?s go|got it|enable|allow|done|finish|yes|set up|sounds good|agree|begin|watch/i,
            ONBOARDING_SCOPE, false
          );
          if (!clicked) clicked = await clickSelector(page, '.mz-task-card', ONBOARDING_SCOPE);
          // Deferral buttons only after one quiet iteration — gives real CTAs
          // a render cycle before settling for skip/later affordances.
          if (!clicked && idleIters >= 1) clicked = await clickButtonMatching(
            page,
            /continue|next|start|install|let'?s go|got it|enable|allow|done|finish|yes|set up|sounds good|agree|begin|watch|skip/i,
            ONBOARDING_SCOPE, true
          );
          if (clicked) {
            console.log(`[verify-onboarding] step ${step}: clicked "${clicked}"`);
            await sleep(900);
          }
        } else {
          await sleep(1200);
        }
        const fp = await domFingerprint(page, ONBOARDING_SCOPE);
        idleIters = (fp === lastFp && !clicked && !state.busy) ? idleIters + 1 : 0;
        lastFp = fp;
        if (!clicked && !state.busy && idleIters >= 3) {
          console.log(`[verify-onboarding] no CTA and no progress for ${idleIters} steps — walk ends here`);
          break;
        }
        await sleep(700);
      }

      await page.screenshot({ path: shotPath('zz-final.png') });
      if (!result.reached_end) throw new Error('Onboarding stopped before reaching the finale.');
      if (!result.first_task_done) throw new Error('Onboarding did not complete a verified free first task.');
      if (result.page_errors.length) throw new Error('Onboarding had browser errors: ' + result.page_errors.join('; '));
      console.log(`ONBOARDING_RESULT ${JSON.stringify(result)}`);
      process.exitCode = 0;
    })()]);
  } catch (err) {
    console.log(`[verify-onboarding] FAILED: ${err.message}`);
    console.log(`ONBOARDING_RESULT ${JSON.stringify(result)}`);
    process.exitCode = 1;
  } finally {
    clearTimeout(deadline);
    if (browser) await browser.close();
  }
})();
