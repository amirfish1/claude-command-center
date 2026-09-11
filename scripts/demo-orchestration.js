#!/usr/bin/env node
// Record a live demo of the Orchestration tab: real session, real injects,
// real child lanes, captured as a screen recording with a visible cursor.
//
// What it does (about two minutes of wall clock, a few cents of tokens):
//   1. Spawns a demo orchestrator session in this repo (Claude, small task).
//   2. Opens the dashboard in headless Chrome, selects that session, and
//      starts a screencast (webm, via puppeteer + ffmpeg).
//   3. Switches the Executor tier, then taps Delegate: the one-line prompt
//      is injected and sent, the session becomes the orchestrator.
//   4. Spawns two cheap real lanes (Haiku, "say done") under the demo
//      session so the lane map animates on live data: born, working, landed.
//   5. Opens a landed lane from the map: the map stays rooted at the
//      orchestrator, the lane lights up as "here"; taps the root to go back.
//   6. Shift-clicks Verify to show the prompt without sending, then shows
//      the Metadata tab with Files docked at the bottom, and ends.
//
// Usage (from the repo root, CCC running on :8090 or per port.txt):
//   node scripts/demo-orchestration.js
// Env:
//   DEMO_OUT        output path, .webm (default ./demo-orchestration.webm)
//   DEMO_URL        dashboard URL (default: port.txt, else http://127.0.0.1:8090)
//   DEMO_REPO       repo path for the demo session (default: cwd)
//   DEMO_MODEL      orchestrator model (default sonnet-5)
//   DEMO_LANE_MODEL model for the two lanes (default haiku-4-5)
//   DEMO_NO_LANES=1 skip the real lanes and play the built-in Preview instead
//   DEMO_SESSION    reuse an existing session id as the orchestrator (no spawn)
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const repoRoot = path.resolve(__dirname, '..');
const puppeteer = require(path.join(repoRoot, 'require-puppeteer.js'));
const { findChromePath } = require(path.join(repoRoot, 'puppeteer-browser-config.js'));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const OUT = path.resolve(process.env.DEMO_OUT || 'demo-orchestration.webm');
const REPO = process.env.DEMO_REPO || process.cwd();
const MODEL = process.env.DEMO_MODEL || 'sonnet-5';
const LANE_MODEL = process.env.DEMO_LANE_MODEL || 'haiku-4-5';

function resolveUrl() {
  if (process.env.DEMO_URL) return process.env.DEMO_URL;
  try {
    const raw = fs.readFileSync(path.join(os.homedir(), '.claude', 'command-center', 'port.txt'), 'utf8').trim();
    if (/^https?:\/\//.test(raw)) return raw;
    if (/^\d+$/.test(raw)) return 'http://127.0.0.1:' + raw;
  } catch (_) {}
  return 'http://127.0.0.1:8090';
}
const URL_BASE = resolveUrl();

function api(method, p, body) {
  return new Promise((resolve, reject) => {
    const u = new URL(p, URL_BASE);
    const data = body ? JSON.stringify(body) : null;
    const req = http.request(u, {
      method,
      headers: Object.assign({ 'Content-Type': 'application/json' }, data ? { 'Content-Length': Buffer.byteLength(data) } : {}),
    }, (res) => {
      let buf = '';
      res.on('data', (c) => { buf += c; });
      res.on('end', () => {
        try { resolve({ status: res.statusCode, json: JSON.parse(buf || 'null') }); }
        catch (e) { resolve({ status: res.statusCode, json: null, text: buf }); }
      });
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

async function spawn(payload) {
  const r = await api('POST', '/api/sessions/spawn', payload);
  if (r.status !== 200 || !r.json || !r.json.ok) throw new Error('spawn failed: ' + JSON.stringify(r.json || r.text));
  let sid = r.json.session_id;
  const spawnId = r.json.spawn_id;
  for (let i = 0; !sid && i < 30; i++) {
    await sleep(2000);
    const reg = await api('GET', '/api/sessions/spawned');
    const row = (reg.json || []).find((x) => String(x.spawn_id) === String(spawnId));
    if (row && row.session_id) sid = row.session_id;
  }
  if (!sid) throw new Error('spawn ' + spawnId + ' never produced a session id');
  return sid;
}

// Demo chrome injected into the page: a cursor that glides to targets and a
// caption strip along the bottom. Pure DOM, removed with the page.
const DEMO_CHROME = () => {
  if (document.getElementById('demoCursor')) return;
  const c = document.createElement('div');
  c.id = 'demoCursor';
  c.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24"><path d="M4 2l16 9-7 1.5L9.5 21z" fill="#fff" stroke="#000" stroke-width="1.5" stroke-linejoin="round"/></svg>';
  Object.assign(c.style, {
    position: 'fixed', left: '0px', top: '0px', zIndex: 2147483647, pointerEvents: 'none',
    transition: 'left 700ms cubic-bezier(.2,.8,.2,1), top 700ms cubic-bezier(.2,.8,.2,1)',
    filter: 'drop-shadow(0 2px 4px rgba(0,0,0,.6))',
  });
  document.body.appendChild(c);
  const ring = document.createElement('div');
  ring.id = 'demoRing';
  Object.assign(ring.style, {
    position: 'fixed', width: '36px', height: '36px', marginLeft: '-14px', marginTop: '-14px',
    border: '2px solid #58a6ff', borderRadius: '999px', opacity: '0', zIndex: 2147483646, pointerEvents: 'none',
  });
  document.body.appendChild(ring);
  const cap = document.createElement('div');
  cap.id = 'demoCaption';
  Object.assign(cap.style, {
    position: 'fixed', left: '50%', bottom: '28px', transform: 'translateX(-50%)', zIndex: 2147483645,
    padding: '10px 18px', borderRadius: '10px', background: 'rgba(13,17,23,.92)', color: '#e6edf3',
    border: '1px solid rgba(88,166,255,.5)', font: '600 15px/1.35 -apple-system, Inter, system-ui, sans-serif',
    maxWidth: '760px', textAlign: 'center', opacity: '0', transition: 'opacity 300ms ease', pointerEvents: 'none',
    boxShadow: '0 8px 30px rgba(0,0,0,.5)',
  });
  document.body.appendChild(cap);
  window.__demo = {
    moveTo(x, y) { c.style.left = x + 'px'; c.style.top = y + 'px'; },
    pulse(x, y) {
      ring.style.left = x + 'px'; ring.style.top = y + 'px';
      ring.animate([{ opacity: 0.9, transform: 'scale(.4)' }, { opacity: 0, transform: 'scale(1.6)' }], { duration: 500, easing: 'ease-out' });
    },
    caption(t) { if (!t) { cap.style.opacity = '0'; return; } cap.textContent = t; cap.style.opacity = '1'; },
  };
};

async function main() {
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  console.log('[demo] dashboard', URL_BASE, '· repo', REPO, '· out', OUT);

  // 1. The demo orchestrator session.
  let demoSid = process.env.DEMO_SESSION || '';
  if (demoSid) console.log('[demo] reusing demo session', demoSid);
  else console.log('[demo] spawning demo orchestrator (' + MODEL + ')');
  if (!demoSid) demoSid = await spawn({
    prompt: 'You are the demo session for CCC Orchestration. Task under consideration: add a `--help` flag to `snapshot.js` that prints the env vars it understands. '
      + 'Do nothing yet. Wait for orchestration playbooks from the dashboard and follow them exactly.',
    repo_path: REPO,
    engine: 'claude',
    model: MODEL,
    reasoning_effort: 'low',
    name: 'Orchestration demo',
  });
  console.log('[demo] demo session', demoSid);

  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox', '--hide-scrollbars'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });
  await page.evaluateOnNewDocument(() => {
    try {
      localStorage.setItem('ccc-status-rail-tab', 'orchestration');
      localStorage.setItem('ccc-tour-done', '1');
      localStorage.setItem('ccc-orch-executor', 'sonnet-5');
    } catch (_) {}
  });
  await page.goto(URL_BASE, { waitUntil: 'load', timeout: 60000 });
  await page.waitForNetworkIdle({ idleTime: 750, timeout: 6000 }).catch(() => {});
  await page.evaluate(() => { document.querySelectorAll('.pwa-install-banner, .fft-backdrop, .fft-modal').forEach((el) => el.remove()); });
  await page.evaluate(DEMO_CHROME);

  const el = async (sel) => {
    const h = await page.waitForSelector(sel, { timeout: 30000 });
    await h.evaluate((n) => n.scrollIntoView({ block: 'nearest' }));
    return h;
  };
  const glide = async (sel, holdMs = 700) => {
    const h = await el(sel);
    const b = await h.boundingBox();
    const x = b.x + b.width / 2, y = b.y + b.height / 2;
    await page.evaluate((x, y) => window.__demo.moveTo(x, y), x, y);
    await sleep(holdMs);
    return { h, x, y };
  };
  const tap = async (sel, opts = {}) => {
    const { x, y } = await glide(sel, opts.holdMs);
    await page.evaluate((x, y) => window.__demo.pulse(x, y), x, y);
    await sleep(120);
    if (opts.shift) await page.keyboard.down('Shift');
    await page.mouse.click(x, y);
    if (opts.shift) await page.keyboard.up('Shift');
  };
  const say = async (t, ms = 1800) => { await page.evaluate((t) => window.__demo.caption(t), t); await sleep(ms); };

  // Wait for the demo session to show in the list, then select it.
  const rowSel = '#convList .conv-item[data-id="' + demoSid + '"], #convList .conv-item[data-session-id="' + demoSid + '"]';
  // A fresh spawn can take a minute or two to show up in the list (the
  // transcript has to exist and the list poll has to pick it up); reload
  // periodically so a stale first paint does not hide it.
  for (let i = 0; i < 90; i++) {
    if (await page.$(rowSel)) break;
    await sleep(2000);
    if (i % 15 === 14) {
      await page.reload({ waitUntil: 'load' });
      await page.waitForNetworkIdle({ idleTime: 750, timeout: 6000 }).catch(() => {});
      await page.evaluate(() => { document.querySelectorAll('.pwa-install-banner, .fft-backdrop, .fft-modal').forEach((el) => el.remove()); });
      await page.evaluate(DEMO_CHROME);
    }
  }
  if (!(await page.$(rowSel))) throw new Error('demo session never appeared in the session list');

  // 2. Record.
  const recorder = await page.screencast({ path: OUT });
  console.log('[demo] recording');
  await page.evaluate(() => window.__demo.moveTo(720, 450));
  await say('CCC Orchestration: one tap turns a session into an orchestrator.', 2200);
  await tap(rowSel);
  await sleep(2500);
  await say('The right rail opens on Orchestration. Three playbooks plus an optional status check. Each one is a short prompt.', 2600);
  await tap('[data-rail-tab="orchestration"]');
  await sleep(900);

  // 3. Executor tier, then Delegate for real.
  await say('Delegate has a second tier: the Executor. Pick who does the work.', 1500);
  await tap('[data-orch-executor="gpt-5.6-terra"]');
  await sleep(1300);
  await tap('[data-orch-executor="grok-4.6"]');
  await sleep(1300);
  await tap('[data-orch-executor="sonnet-5"]');
  await sleep(900);
  await say('Verify and Critique pick the other model family automatically. No lane grades its own homework.', 3000);
  await say('Delegate. One sentence goes into the session: "CCC orchestration: delegate to Sonnet 5."', 1400);
  await tap('[data-orch-playbook="delegate"]');
  await sleep(1500);
  await say('The session now owns the plan and spawns CCC lanes for the work. It coordinates; lanes implement.', 5500);

  // 4. Lanes on live data.
  await say('Lane map: the orchestrator on top, every session it spawns hangs below.', 1500);
  await glide('#orchMapWrap', 600);
  let laneConv = null;
  if (process.env.DEMO_NO_LANES === '1') {
    await tap('#orchMapPreview');
    await say('Lanes are born from the orchestrator, pulse while working, and drop to Landed when done.', 13000);
  } else {
    const lanePrompt = (n) => 'You are lane ' + n + ' of a CCC orchestration demo. Reply with exactly: lane ' + n + ' done. Then stop.';
    console.log('[demo] spawning lanes');
    const lanes = [];
    for (const n of [1, 2]) {
      const r = await api('POST', '/api/sessions/spawn', {
        prompt: lanePrompt(n), repo_path: REPO, engine: 'claude', model: LANE_MODEL, reasoning_effort: 'low',
        parent_session_id: demoSid, report_to: demoSid, name: 'Lane ' + n + ' · demo',
      });
      lanes.push(r.json && r.json.session_id);
      await sleep(2500);
    }
    console.log('[demo] lanes', lanes);
    await say('Two real lanes just spawned under it. Born from the orchestrator, purple while working.', 9000);
    // Wait for both to land (up to 90s), keeping the map in view.
    for (let i = 0; i < 45; i++) {
      const state = await page.evaluate(() => ({
        working: document.querySelectorAll('#orchMapLanes .orch-node').length,
        landed: document.querySelectorAll('#orchMapDone .orch-node').length,
      }));
      if (state.landed >= 2) break;
      await sleep(2000);
    }
    await say('Done lanes glide down to Landed. Their reports inject back into the orchestrator.', 4000);
    laneConv = await page.evaluate(() => {
      const el = document.querySelector('#orchMapDone .orch-node[data-conv-id]') || document.querySelector('#orchMapLanes .orch-node[data-conv-id]');
      return el ? el.getAttribute('data-conv-id') : null;
    });
  }

  // 5. Open a lane from the map; the map stays rooted at the orchestrator.
  if (laneConv) {
    await say('Tap a lane to open it. The map stays the same, rooted at the orchestrator; the lane lights up as here.', 1500);
    await tap('.orch-node[data-conv-id="' + laneConv + '"]');
    await sleep(4000);
    await say('Tap the orchestrator to go back.', 1000);
    await tap('.orch-node-root[data-conv-id]');
    await sleep(2500);
  }

  // Verify: paste without sending, for a look.
  await say('Shift-click pastes the prompt without sending. Verify: "CCC orchestration: verify with 5.6 Terra."', 1200);
  await tap('[data-orch-playbook="verify"]', { shift: true });
  await sleep(3500);
  await page.evaluate(() => {
    const t = document.querySelector('.conv-pane .conv-input-bar textarea');
    if (t) { t.value = ''; t.dispatchEvent(new Event('input', { bubbles: true })); }
  });

  // 6. Metadata with Files docked.
  await say('Metadata keeps the session facts, with Files docked at the bottom.', 1200);
  await tap('[data-rail-tab="metadata"]');
  await sleep(3200);
  await tap('[data-rail-tab="orchestration"]');
  await say('Delegate. Verify. Critique. Check status. That is CCC Orchestration.', 3000);
  await say('', 600);
  await recorder.stop();
  await browser.close();
  console.log('[demo] wrote', OUT);
}

main().catch((e) => { console.error('[demo] failed:', e && e.stack || e); process.exit(1); });
