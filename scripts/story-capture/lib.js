// Shared capture engine for the product-story harness.
//
// Used by shot.js (stills) and record.js (cursor-led videos). Both run
// against the seeded static demo bundle (docs/demo/ served over HTTP) or a
// real local CCC server — the engine doesn't care, it just needs a URL.
//
// Key facts baked in (learned from snapshot.js / OPS tickets):
// - CCC polls forever → never wait on networkidle2; use
//   waitForNetworkIdle({ idleTime, timeout }) with a bounded timeout.
// - localStorage must be seeded via evaluateOnNewDocument BEFORE app
//   scripts run (view mode, flow layout, kanban overrides are all
//   localStorage-backed).
// - Chrome for Testing v149 on macOS ARM crashes during screenshot;
//   prefer installed Chrome/Chrome Beta (OPS-4).
'use strict';

const fs = require('fs');
const path = require('path');
const puppeteer = require(path.join(__dirname, '..', '..', 'require-puppeteer.js'));

const DEFAULT_BASE = process.env.CAP_BASE || 'http://127.0.0.1:8877';

function findChromePath() {
  if (process.env.SNAPSHOT_CHROME) return process.env.SNAPSHOT_CHROME;
  if (process.env.CAP_CHROME) return process.env.CAP_CHROME;
  const macs = [
    '/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ];
  for (const p of macs) {
    try { fs.accessSync(p, fs.constants.X_OK); return p; } catch (_) {}
  }
  return undefined; // puppeteer's bundled Chrome for Testing
}

async function launchBrowser() {
  const chromePath = findChromePath();
  if (chromePath) console.log(`[capture] chrome: ${path.basename(chromePath)}`);
  return puppeteer.launch({
    executablePath: chromePath,
    args: ['--no-sandbox', '--hide-scrollbars', '--force-device-scale-factor=1'],
  });
}

// Force demo mode with an explicit fixture base BEFORE any page script runs.
// This is how we point the CURRENT static/index.html (served from the repo
// root) at the seeded docs/demo/api fixtures: installDemoMode() in app.js
// reads window.__CCC_DEMO__ / window.__CCC_DEMO_FIXTURE_BASE__ at load time.
async function forceDemoFixtures(page, fixtureBase) {
  await page.evaluateOnNewDocument((fb) => {
    window.__CCC_DEMO__ = true;
    window.__CCC_DEMO_FIXTURE_BASE__ = fb;
  }, fixtureBase);
  console.log(`[capture] demo fixtures forced: ${fixtureBase}`);
}

// Seed localStorage before any page script runs. `entries` is a plain object;
// non-string values are JSON-stringified (matches how the app persists maps).
async function seedLocalStorage(page, entries) {
  if (!entries || !Object.keys(entries).length) return;
  await page.evaluateOnNewDocument((data) => {
    try {
      for (const [k, v] of Object.entries(data)) {
        localStorage.setItem(k, typeof v === 'string' ? v : JSON.stringify(v));
      }
    } catch (_) { /* localStorage unavailable pre-navigation — ignore */ }
  }, entries);
  console.log(`[capture] seeded ${Object.keys(entries).length} localStorage keys`);
}

// Navigate and give in-flight fetches a bounded window to settle.
async function gotoAndSettle(page, url, { settleMs = 4000 } = {}) {
  await page.goto(url, { waitUntil: 'load', timeout: 30000 });
  await page.waitForNetworkIdle({ idleTime: 750, timeout: settleMs }).catch(() => {});
}

// Remove the demo-mode "This is a static demo" toast so captures are clean.
// Keeps removing it (it can re-appear on stubbed POSTs) via a MutationObserver.
async function suppressDemoBanner(page) {
  await page.evaluate(() => {
    const KILL = () => {
      const el = document.getElementById('__ccc_demo_ro_banner__');
      if (el) el.remove();
    };
    KILL();
    if (!window.__capBannerKiller) {
      window.__capBannerKiller = new MutationObserver(KILL);
      window.__capBannerKiller.observe(document.body, { childList: true });
    }
  });
}

// ── Synthetic cursor ───────────────────────────────────────────────────────
// A ~20px dark pointer with a soft shadow, animated in-page with rAF easing
// so the video shows smooth motion. Node keeps a mirror of the position so
// real page.mouse events fire at the same coordinates.

const CURSOR_JS = `
(() => {
  if (window.__capCursor) return;
  const el = document.createElement('div');
  el.id = '__cap_cursor__';
  el.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
    + '<path d="M5.5 3.2 L5.5 17.5 L9.1 14.4 L11.4 19.8 L13.8 18.8 L11.5 13.5 L16.3 13.2 Z"'
    + ' fill="#1b1f27" stroke="#f5f6f8" stroke-width="1.4" stroke-linejoin="round"/></svg>';
  el.style.cssText = [
    'position:fixed', 'left:0', 'top:0', 'width:22px', 'height:22px',
    'z-index:2147483647', 'pointer-events:none', 'margin:0',
    'filter:drop-shadow(0 2px 5px rgba(0,0,0,0.45))',
    'transform:translate(-4px,-2px)', 'transition:none', 'will-change:left,top',
  ].join(';');
  (document.body || document.documentElement).appendChild(el);
  const state = { x: 0, y: 0, raf: 0 };
  function place(x, y) {
    state.x = x; state.y = y;
    el.style.left = x + 'px';
    el.style.top = y + 'px';
  }
  function easeInOutCubic(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }
  // Animate to (x,y) over ms; onStep (optional name of a window fn) gets
  // called with the current position each frame — used to stream dragover
  // events along the path.
  function moveTo(x, y, ms, onStepName) {
    cancelAnimationFrame(state.raf);
    const x0 = state.x, y0 = state.y;
    const t0 = performance.now();
    return new Promise((resolve) => {
      function frame(now) {
        const t = Math.min(1, (now - t0) / ms);
        const k = easeInOutCubic(t);
        const cx = x0 + (x - x0) * k;
        const cy = y0 + (y - y0) * k;
        place(cx, cy);
        if (onStepName && typeof window[onStepName] === 'function') {
          try { window[onStepName](cx, cy); } catch (_) {}
        }
        if (t < 1) state.raf = requestAnimationFrame(frame);
        else resolve();
      }
      state.raf = requestAnimationFrame(frame);
    });
  }
  function ripple() {
    const r = document.createElement('div');
    r.style.cssText = [
      'position:fixed', 'left:' + (state.x - 14) + 'px', 'top:' + (state.y - 14) + 'px',
      'width:28px', 'height:28px', 'border-radius:50%',
      'border:2.5px solid rgba(90,150,255,0.9)', 'background:rgba(90,150,255,0.22)',
      'z-index:2147483646', 'pointer-events:none', 'opacity:1',
      'transform:scale(0.35)',
      'transition:transform 380ms ease-out, opacity 420ms ease-out',
    ].join(';');
    document.body.appendChild(r);
    requestAnimationFrame(() => {
      r.style.transform = 'scale(1.5)';
      r.style.opacity = '0';
    });
    setTimeout(() => r.remove(), 520);
  }
  function pressed(on) {
    el.style.transform = on ? 'translate(-4px,-2px) scale(0.85)' : 'translate(-4px,-2px)';
  }
  window.__capCursor = { place, moveTo, ripple, pressed, state };
})();
`;

async function installCursor(page, x = 40, y = 40) {
  await page.evaluate(CURSOR_JS);
  await page.evaluate((px, py) => window.__capCursor.place(px, py), x, y);
  await page.mouse.move(x, y);
}

// ── Action context ─────────────────────────────────────────────────────────
// Helpers handed to flow modules. All coordinate math happens here so flows
// stay declarative ("move to selector, click, drag A onto B").

function makeCtx(page) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function centerOf(selector) {
    const box = await page.evaluate((sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height };
    }, selector);
    if (!box) throw new Error(`centerOf: selector not found: ${selector}`);
    return box;
  }

  async function move(target, { duration = 600 } = {}) {
    const pt = typeof target === 'string' ? await centerOf(target) : target;
    await page.evaluate(
      (x, y, ms) => window.__capCursor.moveTo(x, y, ms),
      pt.x, pt.y, duration
    );
    await page.mouse.move(pt.x, pt.y);
    return pt;
  }

  async function click(target, { duration = 600, settle = 250 } = {}) {
    const pt = await move(target, { duration });
    await page.evaluate(() => { window.__capCursor.pressed(true); window.__capCursor.ripple(); });
    await page.mouse.down();
    await sleep(90);
    await page.mouse.up();
    await page.evaluate(() => window.__capCursor.pressed(false));
    await sleep(settle);
    return pt;
  }

  // HTML5 drag-and-drop with synthetic DragEvents, cursor-synced.
  // Puppeteer's raw mouse cannot start a native HTML5 drag, so we dispatch
  // dragstart/dragover/drop/dragend ourselves with a shared DataTransfer —
  // exactly the events CCC's kanban/split-pane listeners consume. The cursor
  // animates along the path and dragover fires each frame so drop-highlights
  // light up like a real drag.
  //
  // The drop target is resolved AFTER dragstart (two rAFs later): some drop
  // zones (e.g. the split-pane `.drop-zone.right`) only appear once a drag
  // is in flight.
  // Options:
  //   duration — total drag animation ms
  //   hold     — "grab" pause after mousedown, before movement
  //   via      — optional waypoint selector or {x,y}: the drag passes through
  //              it first, streaming dragover along the way. Needed when the
  //              real drop zone only becomes visible once a drag is over a
  //              region (e.g. split-pane `.drop-zone.right` appears when the
  //              drag crosses the open conversation).
  async function dragHTML5(fromSel, toSel, { duration = 1100, hold = 350, via = null } = {}) {
    const from = await move(fromSel, { duration: 700 });
    await page.evaluate(() => window.__capCursor.pressed(true));
    await sleep(hold); // visible "grab" beat
    const to = await page.evaluate(
      async (fSel, tSel, fx, fy, ms, viaSpec) => {
        const fromEl = document.querySelector(fSel);
        if (!fromEl) throw new Error('dragHTML5: source missing: ' + fSel);
        const dt = new DataTransfer();
        const fire = (el, type, x, y, cancelable = true) => {
          const ev = new DragEvent(type, {
            bubbles: true, cancelable, composed: true,
            clientX: Math.round(x), clientY: Math.round(y), dataTransfer: dt,
          });
          el.dispatchEvent(ev);
        };
        const raf2 = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
        const centerIfVisible = (el) => {
          const r = el.getBoundingClientRect();
          if (r.width < 4 && r.height < 4) return null;
          return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
        };
        fire(fromEl, 'dragstart', fx, fy);
        await raf2();
        // Stream dragover along the animated path so drop zones/highlights
        // react like a real drag.
        window.__capDragStep = (cx, cy) => {
          const over = document.elementFromPoint(cx, cy);
          if (over) fire(over, 'dragover', cx, cy);
        };
        let spent = 0;
        if (viaSpec) {
          let v = null;
          if (typeof viaSpec === 'string') {
            const viaEl = document.querySelector(viaSpec);
            if (viaEl) v = centerIfVisible(viaEl);
          } else v = viaSpec;
          if (v) {
            const legMs = Math.round(ms * 0.55);
            await window.__capCursor.moveTo(v.x, v.y, legMs, '__capDragStep');
            spent = legMs;
          }
        }
        // Resolve the target; poll up to ~800ms for it to exist AND be laid
        // out (non-zero rect) — drag-revealed zones can take a few frames.
        let toEl = null, pt = null;
        const deadline = performance.now() + 800;
        for (;;) {
          toEl = document.querySelector(tSel);
          if (toEl) pt = centerIfVisible(toEl);
          if (pt || performance.now() > deadline) break;
          await raf2();
        }
        if (!toEl) {
          delete window.__capDragStep;
          fire(fromEl, 'dragend', fx, fy, false);
          throw new Error('dragHTML5: target missing after dragstart: ' + tSel);
        }
        if (!pt) {
          // Element exists but is not laid out — drop on it in place without
          // sending the cursor to a bogus (0,0) rect.
          const s = window.__capCursor.state;
          pt = { x: s.x, y: s.y };
        } else {
          await window.__capCursor.moveTo(pt.x, pt.y, Math.max(250, ms - spent), '__capDragStep');
        }
        delete window.__capDragStep;
        fire(toEl, 'dragenter', pt.x, pt.y);
        fire(toEl, 'dragover', pt.x, pt.y);
        fire(toEl, 'drop', pt.x, pt.y);
        fire(fromEl, 'dragend', pt.x, pt.y, false);
        return pt;
      },
      fromSel, toSel, from.x, from.y, duration,
      typeof via === 'string' ? via : (via ? { x: via.x, y: via.y } : null)
    );
    await page.mouse.move(to.x, to.y);
    await page.evaluate(() => window.__capCursor.pressed(false));
    await sleep(300);
  }

  // Smooth scroll an element (or the page). dy scrolls vertically, dx
  // horizontally — pass either or both.
  async function scrollEl(selector, dy, { duration = 800, dx = 0 } = {}) {
    await page.evaluate((sel, dY, dX, ms) => new Promise((resolve) => {
      const el = sel ? document.querySelector(sel) : document.scrollingElement;
      if (!el) return resolve();
      const startTop = el.scrollTop, startLeft = el.scrollLeft;
      const t0 = performance.now();
      const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
      (function frame(now) {
        const t = Math.min(1, (now - t0) / ms);
        if (dY) el.scrollTop = startTop + dY * ease(t);
        if (dX) el.scrollLeft = startLeft + dX * ease(t);
        if (t < 1) requestAnimationFrame(frame); else resolve();
      })(performance.now());
    }), selector, dy || 0, dx, duration);
  }

  async function type(selector, text, { perChar = 55 } = {}) {
    await click(selector, { duration: 500 });
    await page.type(selector, text, { delay: perChar });
  }

  // Pointer-based drag (mouse down + move + up) for UI wired to pointer
  // events rather than HTML5 DnD — Flow nodes, resizers, canvas pans. The
  // cursor is stepped in lockstep with real mouse.move events.
  async function pressDrag(fromTarget, toTarget, { duration = 900, steps = 24, hold = 250 } = {}) {
    const from = typeof fromTarget === 'string' ? await centerOf(fromTarget) : fromTarget;
    const to = typeof toTarget === 'string' ? await centerOf(toTarget) : toTarget;
    await move(from, { duration: 600 });
    await page.evaluate(() => window.__capCursor.pressed(true));
    await page.mouse.down();
    await sleep(hold);
    const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
    for (let i = 1; i <= steps; i++) {
      const k = ease(i / steps);
      const x = from.x + (to.x - from.x) * k;
      const y = from.y + (to.y - from.y) * k;
      await page.mouse.move(x, y);
      await page.evaluate((px, py) => window.__capCursor.place(px, py), x, y);
      await sleep(duration / steps);
    }
    await page.mouse.up();
    await page.evaluate(() => window.__capCursor.pressed(false));
    await sleep(250);
  }

  // Center of the first element matching `scopeSel` whose trimmed text
  // includes `text` (case-insensitive). For buttons with no stable id.
  async function findByText(scopeSel, text) {
    const pt = await page.evaluate((sel, needle) => {
      const els = [...document.querySelectorAll(sel)];
      const el = els.find(e => (e.textContent || '').trim().toLowerCase().includes(needle.toLowerCase()));
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }, scopeSel, text);
    if (!pt) throw new Error(`findByText: no ${scopeSel} containing "${text}"`);
    return pt;
  }

  return {
    page,
    sleep,
    pause: sleep,
    centerOf,
    move,
    click,
    dragHTML5,
    pressDrag,
    findByText,
    scrollEl,
    type,
    waitFor: (sel, timeout = 8000) => page.waitForSelector(sel, { timeout }),
    eval: (fn, ...args) => page.evaluate(fn, ...args),
    suppressBanner: () => suppressDemoBanner(page),
    // Scene cut: fade to the app background, apply localStorage overrides,
    // reload, settle, re-suppress the banner, re-install the cursor, fade
    // back in. Used where the real UI has no click affordance to change a
    // persisted preference (e.g. list -> board view).
    async reloadWith(entries, { settleMs = 1200, cursorAt } = {}) {
      const pos = await page.evaluate(() => {
        const d = document.createElement('div');
        d.id = '__cap_fade__';
        d.style.cssText = 'position:fixed;inset:0;background:#0d1117;opacity:0;'
          + 'transition:opacity 320ms ease;z-index:2147483647;pointer-events:none';
        document.body.appendChild(d);
        requestAnimationFrame(() => { d.style.opacity = '1'; });
        const s = (window.__capCursor && window.__capCursor.state) || { x: 60, y: 60 };
        return { x: s.x, y: s.y };
      });
      await sleep(420);
      // The initial seed was registered with evaluateOnNewDocument, which
      // re-runs on EVERY navigation — including this reload — and would
      // clobber plain localStorage writes. Register the overrides as a
      // second on-new-document script: it runs after the first one, so the
      // overrides win on this reload and any later ones.
      await page.evaluateOnNewDocument((data) => {
        try {
          for (const [k, v] of Object.entries(data || {})) {
            localStorage.setItem(k, typeof v === 'string' ? v : JSON.stringify(v));
          }
        } catch (_) { /* pre-navigation localStorage unavailable — ignore */ }
      }, entries || {});
      await Promise.all([
        page.waitForNavigation({ waitUntil: 'load', timeout: 30000 }),
        page.evaluate(() => location.reload()),
      ]);
      // Fade back in as soon as real content is on screen — a long dark hold
      // reads as dead time in the recording.
      await page.waitForFunction(
        () => document.querySelector('.conv-item, .kanban-card, .flow-node'),
        { timeout: 5000 }
      ).catch(() => {});
      await page.waitForNetworkIdle({ idleTime: 400, timeout: settleMs }).catch(() => {});
      await suppressDemoBanner(page);
      const at = cursorAt || pos;
      await installCursor(page, at.x, at.y);
      await page.evaluate(() => {
        const d = document.createElement('div');
        d.style.cssText = 'position:fixed;inset:0;background:#0d1117;opacity:1;'
          + 'transition:opacity 420ms ease;z-index:2147483647;pointer-events:none';
        document.body.appendChild(d);
        requestAnimationFrame(() => {
          d.style.opacity = '0';
          setTimeout(() => d.remove(), 500);
        });
      });
      await sleep(450);
    },
    // Re-allow the demo "read-only" toast (V-14 shows it honestly when a
    // mutating action is stubbed). Disconnects the suppression observer.
    allowBanner: () => page.evaluate(() => {
      if (window.__capBannerKiller) {
        window.__capBannerKiller.disconnect();
        delete window.__capBannerKiller;
      }
    }),
  };
}

// Parse "1440x900" → { width, height }.
function parseViewport(spec, fallback = { width: 1440, height: 900 }) {
  if (!spec) return fallback;
  const m = /^(\d+)x(\d+)$/.exec(String(spec).trim());
  if (!m) throw new Error(`bad viewport spec: ${spec} (expected WxH)`);
  return { width: Number(m[1]), height: Number(m[2]) };
}

// Minimal argv parser: --key value / --flag.
function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith('--')) out[key] = true;
      else { out[key] = next; i++; }
    } else out._.push(a);
  }
  return out;
}

function loadJsonMaybe(p) {
  if (!p) return null;
  return JSON.parse(fs.readFileSync(path.resolve(p), 'utf8'));
}

function resolveUrl(base, urlPath) {
  if (/^https?:\/\//.test(urlPath)) return urlPath;
  return base.replace(/\/+$/, '') + '/' + String(urlPath || '').replace(/^\/+/, '');
}

module.exports = {
  DEFAULT_BASE,
  launchBrowser,
  forceDemoFixtures,
  seedLocalStorage,
  gotoAndSettle,
  suppressDemoBanner,
  installCursor,
  makeCtx,
  parseViewport,
  parseArgs,
  loadJsonMaybe,
  resolveUrl,
};
