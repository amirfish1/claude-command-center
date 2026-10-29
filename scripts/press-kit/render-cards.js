#!/usr/bin/env node
// Press-kit share cards: renders docs/press/cards/*.png with the REAL card code
// (window.CCCShare.cardModel + drawCard in static/throughput.html) fed a
// synthetic, deterministic 365-day payload. No real usage data is read: every
// /api/* request is answered by the stub below, so the pixels can only ever
// show demo numbers.
//
//   python3 -m http.server 8877 --directory <repo root> &
//   node scripts/press-kit/render-cards.js            # writes docs/press/cards/
//   node scripts/press-kit/render-cards.js --out /tmp/cards
//   node scripts/press-kit/render-cards.js --only month   # just the monthly cards
'use strict';

const fs = require('fs');
const path = require('path');
const { launchBrowser, DEFAULT_BASE } = require('../story-capture/lib.js');
const { syntheticPayload } = require('./synthetic-payload.js');

const ROOT = path.join(__dirname, '..', '..');
const args = process.argv.slice(2);
const outIdx = args.indexOf('--out');
const OUT = outIdx >= 0 ? path.resolve(args[outIdx + 1]) : path.join(ROOT, 'docs', 'press', 'cards');
const onlyIdx = args.indexOf('--only');
const ONLY = onlyIdx >= 0 ? args[onlyIdx + 1] : '';

// Fixed "today" so re-renders are byte-stable; the heatmap ends on this day.
const TODAY = '2026-10-18';

const CARDS = [
  { file: 'card-tokens-1200x630.png', size: 'wide', state: { period: 'month', metric: 'tokens' } },
  { file: 'card-tokens-1080x1080.png', size: 'square', state: { period: 'month', metric: 'tokens' } },
  { file: 'card-saved-1200x630.png', size: 'wide', state: { period: 'month', metric: 'saved' } },
  { file: 'card-saved-1080x1080.png', size: 'square', state: { period: 'month', metric: 'saved' } },
  // Monthly "My October" card as it looks on Nov 1, when the wave goes out.
  { file: 'card-month-1200x630.png', size: 'wide', today: '2026-11-01', state: { period: 'cal', metric: 'tokens', cost: true } },
  { file: 'card-month-1080x1080.png', size: 'square', today: '2026-11-01', state: { period: 'cal', metric: 'tokens', cost: true } },
].filter((c) => !ONLY || c.file.includes(ONLY));

const payloadFor = (today) => {
  const [y, m, d] = today.split('-').map(Number);
  return syntheticPayload(new Date(y, m - 1, d));
};

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const payload = payloadFor(TODAY);
  const browser = await launchBrowser();
  try {
    const page = await browser.newPage();
    await page.setRequestInterception(true);
    page.on('request', (req) => {
      const u = new URL(req.url());
      if (!u.pathname.startsWith('/api/')) return req.continue();
      const body = u.pathname === '/api/throughput/share' ? payload : { ok: true };
      req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.goto(DEFAULT_BASE + '/static/throughput.html', { waitUntil: 'load', timeout: 30000 });
    await page.waitForFunction(() => window.CCCShare && window.CCCShare.drawCard, { timeout: 15000 });
    await page.evaluate(() => document.fonts && document.fonts.ready);
    for (const card of CARDS) {
      const dataUrl = await page.evaluate((p, st, size, today) => {
        const base = { pseudo: true, streak: true, cost: false, saved: true, engines: false, name: 'demo-otter', month: '' };
        const [y, m, d] = today.split('-').map(Number);
        const model = window.CCCShare.cardModel(p, Object.assign(base, st), new Date(y, m - 1, d), null);
        const c = document.createElement('canvas');
        window.CCCShare.drawCard(c, model, size);
        return c.toDataURL('image/png');
      }, card.today ? payloadFor(card.today) : payload, card.state, card.size, card.today || TODAY);
      const file = path.join(OUT, card.file);
      fs.writeFileSync(file, Buffer.from(dataUrl.split(',')[1], 'base64'));
      console.log(`[press] ${path.relative(ROOT, file)}`);
    }
  } finally {
    await browser.close();
  }
})().catch((err) => { console.error(err); process.exit(1); });
