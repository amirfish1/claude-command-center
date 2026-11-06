#!/usr/bin/env node
// End-to-end check of the browser demo: boots the built bundle with no backend
// behind it, taps real decision chips and the approval card, and saves
// screenshots. Fails on page errors, or on any request to a real /api path.
//
//   python3 scripts/build_demo.py
//   python3 -m http.server 8878 --directory out/try-demo
//   node scripts/verify-try-demo.js [--url http://127.0.0.1:8878/] [--out dir]
'use strict';
const path = require('path');
const fs = require('fs');
const repoRoot = path.resolve(__dirname, '..');
const puppeteer = require(path.join(repoRoot, 'require-puppeteer.js'));
const { findChromePath } = require(path.join(repoRoot, 'puppeteer-browser-config.js'));

const arg = (name, fallback) => {
  const i = process.argv.indexOf(name);
  return i > 0 ? process.argv[i + 1] : fallback;
};
const URL_ = arg('--url', 'http://127.0.0.1:8878/');
const OUT = path.resolve(arg('--out', 'out/try-demo-shots'));
fs.mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const failures = [];
function check(name, ok, detail) {
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? '  ' + detail : ''));
  if (!ok) failures.push(name);
}

async function open(browser, viewport, label) {
  const page = await browser.newPage();
  await page.setViewport(viewport);
  const problems = [];
  const offsite = new Set();
  page.on('pageerror', (e) => problems.push('pageerror: ' + String(e.message).slice(0, 200)));
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (u.pathname.includes('/api/')) problems.push('real api request: ' + r.url().slice(0, 120));
    if (u.protocol.startsWith('http') && u.origin !== new URL(URL_).origin) offsite.add(u.hostname);
  });
  page.on('response', (r) => {
    if (r.status() >= 400 && r.url().startsWith(URL_)) problems.push(r.status() + ' ' + r.url().slice(0, 120));
  });
  const started = Date.now();
  await page.goto(URL_, { waitUntil: 'load', timeout: 60000 });
  await page.waitForSelector('#convList .conv-quick-action', { timeout: 30000 });
  const readyMs = Date.now() - started;
  await sleep(1200);
  console.log(`[${label}] decision chips on screen after ${readyMs} ms`);
  return { page, problems, offsite, readyMs };
}
// Rows carry each chip twice (wide and narrow layouts); act on the visible one.
const firstChip = (page) => page.evaluate(() => {
  const b = [...document.querySelectorAll('#convList .conv-quick-action')].find((x) => x.getClientRects().length);
  if (!b) return null;
  const r = b.getBoundingClientRect();
  return { sid: b.closest('.conv-item').dataset.sessionId, label: b.textContent.trim(), x: r.x + r.width / 2, y: r.y + r.height / 2 };
});
const rowWorking = (page, sid) => page.evaluate((id) => !!document.querySelector(
  '#convList .conv-item[data-session-id="' + id + '"] .conv-outcome.is-working'), sid);
const counts = (page) => page.evaluate(() => window.__tryDemo.counts());
const rowText = (page, sid) => page.evaluate((id) => {
  const r = document.querySelector('#convList .conv-item[data-session-id="' + id + '"]');
  return r ? r.innerText.replace(/\s+/g, ' ') : '';
}, sid);

(async () => {
  const browser = await puppeteer.launch({ headless: 'new', executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const { page, problems, offsite, readyMs } = await open(browser, { width: 1440, height: 900 }, 'desktop');
    await page.screenshot({ path: path.join(OUT, '01-fleet-and-decisions.png') });
    const start = await counts(page);
    check('many agents at once', start.total >= 20 && start.working >= 8, JSON.stringify(start));
    check('several need you', start.needsYou >= 5);
    check('ribbon says it is a demo', (await page.$eval('#tryDemoRibbon', (e) => e.innerText)).includes('Invented'));
    check('transcript opened on the first decision', await page.$eval('.ssb-next', (e) => /Needs you/i.test(e.innerText)).catch(() => false));

    // One tap on the sidebar chip.
    const chip = await firstChip(page);
    const sid = chip.sid;
    const before = await rowText(page, sid);
    await page.mouse.click(chip.x, chip.y);
    // The turn lasts 4.6 s. Time the working state inside the page, because
    // sampling across the browser boundary is too coarse on a busy machine.
    const tapped = Date.now();
    await page.evaluate((id) => {
      const watch = { first: null, droppedMs: 0 };
      const started = performance.now();
      let last = started;
      const timer = setInterval(() => {
        const row = document.querySelector(`#convList .conv-item[data-session-id="${id}"]`);
        const working = !!(row && row.querySelector('.conv-outcome.is-working'));
        const at = performance.now();
        if (working && watch.first === null) watch.first = Math.round(at - started);
        if (!working && watch.first !== null) watch.droppedMs += at - last;
        last = at;
        if (at - started > 4200) clearInterval(timer);
      }, 20);
      window.__rowWatch = watch;
    }, sid);
    await sleep(1600);
    await page.screenshot({ path: path.join(OUT, '02-agent-back-at-work.png') });
    const during = await rowText(page, sid);
    await sleep(Math.max(0, 4400 - (Date.now() - tapped)));
    const watch = await page.evaluate(() => window.__rowWatch);
    check('tap puts the agent back to work', watch.first !== null && watch.first < 1000 && during !== before,
      `${watch.first} ms: ${during.slice(0, 70)}`);
    // About 0.1 s remains when the first reply lands; the row text does not change.
    check('row stays working through the turn', watch.droppedMs < 300, `${Math.round(watch.droppedMs)} ms not working`);
    await sleep(Math.max(0, 6800 - (Date.now() - tapped)));
    await page.screenshot({ path: path.join(OUT, '03-decision-landed.png') });
    const after = await rowText(page, sid);
    const transcript = await page.$eval('#convSplit', (e) => e.innerText).catch(() => '');
    check('row lands without chips', !after.includes(chip.label) && !await rowWorking(page, sid), after.slice(0, 90));
    check('reply and result reach the transcript', /Round once on the order total/.test(transcript) && /Moved rounding/.test(transcript));
    const landed = await counts(page);
    check('who-needs-you count falls', landed.needsYou < start.needsYou + 2, JSON.stringify(landed));

    // The blocked approval: open it, approve from the card.
    const approve = await page.evaluate(() => window.__tryDemo.sessions.find((r) => r.spec.kind === 'approve').s.session_id);
    await page.click('#convList .conv-item[data-session-id="' + approve + '"]');
    await sleep(1800);
    await page.screenshot({ path: path.join(OUT, '04-approval-waiting.png') });
    const clicked = await page.evaluate(() => {
      const b = [...document.querySelectorAll('button')].find((x) => /^approve/i.test(x.textContent.trim()) && x.offsetParent);
      if (b) b.click();
      return !!b;
    });
    check('approval card offers Approve', clicked);
    await sleep(1500);
    const approved = await page.evaluate((id) => window.__tryDemo.sessions.find((r) => r.s.session_id === id).s.needs_approval, approve);
    check('approve clears the block', approved === false);

    // A typed reply goes through the real composer.
    const typed = await page.evaluate(() => window.__tryDemo.sessions.find((r) => r.spec.kind === 'decide' && r.s.session_state
      && r.s.session_state.next_actions && !/^none/i.test(r.s.session_state.next_step_user)).s.session_id);
    await page.click('#convList .conv-item[data-session-id="' + typed + '"]');
    await sleep(1200);
    await page.type('#convInput', 'What would you do?');
    await page.keyboard.press('Enter');
    await sleep(6500);
    check('typed reply gets an honest answer', /no real agent read that/i.test(await page.$eval('#convSplit', (e) => e.innerText)));

    // Archiving a finished row removes it from the list.
    const finished = await page.evaluate(() => window.__tryDemo.sessions.find((r) => r.spec.kind === 'ready').s.session_id);
    const archived = await page.evaluate(async (id) => (await (await fetch('/api/conversations/' + id + '/archive', {
      method: 'POST', body: JSON.stringify({ archived: true }) })).json()).archived, finished);
    check('archive updates the fake backend', archived === true);

    // A working session flips to a new decision while you watch.
    const flipped = await page.waitForFunction(
      () => window.__tryDemo.sessions.some((r) => r.landed), { timeout: 90000 }).then(() => true, () => false);
    check('a working agent ends its turn with a new decision', flipped);
    await sleep(1200);
    await page.screenshot({ path: path.join(OUT, '05-new-decision-arrives.png') });
    const pill = await page.evaluate(() => [...document.querySelectorAll('.sidebar *')].some((e) => /^needs you \d/.test(e.textContent.trim()) && e.children.length === 0 && e.getClientRects().length));
    check('footer pill clears after the approval', !pill);

    check('no page errors or real API calls', problems.length === 0, problems.slice(0, 4).join(' | '));
    check('first decisions visible quickly', readyMs < 10000, readyMs + ' ms');
    check('nothing leaves the browser', offsite.size === 0, [...offsite].join(', '));
    await page.close();

    const phone = await open(browser, { width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 }, 'phone');
    await phone.page.screenshot({ path: path.join(OUT, '06-phone.png') });
    const phoneChip = await firstChip(phone.page);
    await phone.page.touchscreen.tap(phoneChip.x, phoneChip.y);
    await sleep(1500);
    await phone.page.screenshot({ path: path.join(OUT, '07-phone-after-tap.png') });
    check('phone tap reaches the fake backend', await phone.page.evaluate(() => window.__tryDemo.taps === 1));
    check('phone: no page errors', phone.problems.length === 0, phone.problems.slice(0, 4).join(' | '));
  } finally {
    await browser.close();
  }
  console.log(failures.length ? `\n${failures.length} FAILED` : '\nALL PASSED');
  process.exit(failures.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
