#!/usr/bin/env node
// Press-kit screenshots: docs/press/screenshots/*.png from the real UI on the
// seeded demo fixtures (docs/demo/api, fake sessions and repos only).
//
// The fixtures carry fixed dates, so the sessions read "20w ago". This runner
// rewrites every fixture response in flight, shifting all timestamps by one
// delta so the newest one lands a few minutes before now. Relative order and
// gaps are kept. The share screen shot gets the synthetic payload from
// render-cards.js, never real usage.
//
//   python3 -m http.server 8877 --directory <repo root> &
//   node scripts/press-kit/shots.js                   # all shots
//   node scripts/press-kit/shots.js --only share --out /tmp/shots
'use strict';

const fs = require('fs');
const path = require('path');
const {
  DEFAULT_BASE, launchBrowser, forceDemoFixtures, seedLocalStorage,
  gotoAndSettle, suppressDemoBanner, installCursor, makeCtx, parseArgs,
} = require('../story-capture/lib.js');
const { LIST } = require('../story-capture/flows/_seeds.js');
const { syntheticPayload } = require('./synthetic-payload.js');

const ROOT = path.join(__dirname, '..', '..');
const FIXTURES = path.join(ROOT, 'docs', 'demo', 'api');
const ISO_RE = /"(20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)(Z|[+-]\d\d:\d\d)?"/g;
const EPOCH_RE = /("(?:mtime|sidecar_ts|last_message_at|last_mtime|synced_at|fetched_at)":\s*)(1[67]\d{8}(?:\.\d+)?)/g;

// Newest session mtime in the fleet list: one delta from it shifts every
// fixture, so the freshest session reads "just now".
function newestFixtureTs() {
  const list = JSON.parse(fs.readFileSync(path.join(FIXTURES, 'conversations', 'list.json'), 'utf8'));
  return Math.max(...list.conversations.map((c) => c.mtime || 0));
}

function rebase(body, delta) {
  return body
    .replace(ISO_RE, (_, ts, tz) => {
      const shifted = new Date(Date.parse(ts + (tz || 'Z')) + delta * 1000).toISOString();
      return '"' + (tz ? shifted : shifted.replace(/Z$/, '')) + '"';
    })
    .replace(EPOCH_RE, (_, key, n) => key + (Number(n) + delta).toFixed(n.includes('.') ? 3 : 0));
}

const SHOTS = {
  // The fleet board with one session's transcript open.
  board: {
    url: '/static/index.html?demo=1',
    viewport: [1440, 900],
    ls: { ...LIST, 'ccc-sidebar-width': '520' },
    async run(ctx) {
      await ctx.pause(700);
      await ctx.click('.conv-item[data-id^="22222222"]', { duration: 300 });
      await ctx.pause(1500);
    },
  },
  // Phone width: the fleet list, as served to a phone over Tailscale/LAN.
  mobile: {
    url: '/static/index.html?demo=1',
    viewport: [390, 844],
    scale: 3,
    ls: { ...LIST, 'ccc-status-rail-collapsed': '1' },
    async run(ctx) { await ctx.pause(900); },
  },
  // The share screen on the throughput page, fed the synthetic payload.
  share: {
    url: '/static/throughput.html?share=1',
    viewport: [1440, 900],
    ls: { 'ccc.share.v1': JSON.stringify({ period: 'month', metric: 'tokens', size: 'wide', name: 'demo-otter' }) },
    stubApi: true,
    async run(ctx) {
      await ctx.waitFor('#share-overlay.open');
      await ctx.pause(1500);
    },
  },
};

(async () => {
  const args = parseArgs(process.argv.slice(2));
  const out = path.resolve(args.out || path.join(ROOT, 'docs', 'press', 'screenshots'));
  const names = args.only ? String(args.only).split(',') : Object.keys(SHOTS);
  const delta = Math.round(Date.now() / 1000 - 180 - newestFixtureTs());
  const payload = JSON.stringify(syntheticPayload(new Date()));
  fs.mkdirSync(out, { recursive: true });
  const browser = await launchBrowser();
  try {
    for (const name of names) {
      const shot = SHOTS[name];
      if (!shot) throw new Error(`unknown shot: ${name}`);
      const page = await browser.newPage();
      await page.setViewport({ width: shot.viewport[0], height: shot.viewport[1], deviceScaleFactor: shot.scale || 2 });
      await page.setRequestInterception(true);
      page.on('request', async (req) => {
        const u = new URL(req.url());
        if (shot.stubApi && u.pathname.startsWith('/api/')) {
          const body = u.pathname === '/api/throughput/share' ? payload : '{"ok":true}';
          return req.respond({ status: 200, contentType: 'application/json', body });
        }
        if (u.pathname.startsWith('/docs/demo/api/') && u.pathname.endsWith('.json')) {
          const file = path.join(ROOT, decodeURIComponent(u.pathname));
          if (file.startsWith(FIXTURES) && fs.existsSync(file)) {
            return req.respond({ status: 200, contentType: 'application/json', body: rebase(fs.readFileSync(file, 'utf8'), delta) });
          }
        }
        return req.continue();
      });
      if (!shot.stubApi) await forceDemoFixtures(page, '/docs/demo/api');
      await seedLocalStorage(page, shot.ls);
      await gotoAndSettle(page, DEFAULT_BASE + shot.url);
      await suppressDemoBanner(page);
      await installCursor(page);
      await shot.run(makeCtx(page));
      await suppressDemoBanner(page);
      await page.evaluate(() => { const c = document.getElementById('__cap_cursor__'); if (c) c.remove(); });
      const file = path.join(out, name + '.png');
      await page.screenshot({ path: file });
      console.log(`[press] ${path.relative(ROOT, file)}`);
      await page.close();
    }
  } finally {
    await browser.close();
  }
})().catch((err) => { console.error('[press] FAILED:', err.message); process.exit(1); });
