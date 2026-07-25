#!/usr/bin/env node
// Screenshot tool for the product-story capture harness.
//
// Captures crisp stills of CCC (demo bundle or live server) at
// deviceScaleFactor 2 by default, with optional localStorage seeding and
// pre-capture actions (click / wait / scroll / drag) or a full flow module.
//
// Usage:
//   node scripts/story-capture/shot.js --url /demo/ --out out.png
//     [--viewport 1440x900]       viewport CSS px (default 1440x900)
//     [--scale 2]                 deviceScaleFactor (default 2 → 2880x1800)
//     [--base http://127.0.0.1:8877]  or env CAP_BASE
//     [--ls path/to/seed.json]    localStorage entries, seeded pre-load
//     [--flow flows/foo.js]       run a flow module's run(ctx) before capture
//     [--actions '<json-array>']  inline declarative actions (see README)
//     [--settle 4000]             ms to let network settle after load
//     [--wait 0]                  extra ms to wait before capture
//     [--full-page]               capture full page height
//     [--keep-banner]             don't suppress the demo-mode toast
//     [--cursor]                  leave the synthetic cursor visible
//
// Exit codes: 0 written; 1 error.
'use strict';

const path = require('path');
const fs = require('fs');
const {
  DEFAULT_BASE, launchBrowser, forceDemoFixtures, seedLocalStorage,
  gotoAndSettle, suppressDemoBanner, installCursor, makeCtx, parseViewport,
  parseArgs, loadJsonMaybe, resolveUrl,
} = require('./lib.js');

async function runActions(ctx, actions) {
  for (const a of actions) {
    switch (a.type) {
      case 'wait': await ctx.pause(a.ms || 500); break;
      case 'waitFor': await ctx.waitFor(a.selector, a.timeout); break;
      case 'click': await ctx.click(a.selector, a); break;
      case 'move': await ctx.move(a.selector || { x: a.x, y: a.y }, a); break;
      case 'drag': await ctx.dragHTML5(a.from, a.to, a); break;
      case 'scroll': await ctx.scrollEl(a.selector || null, a.dy || 400, a); break;
      case 'type': await ctx.type(a.selector, a.text, a); break;
      default: throw new Error(`unknown action type: ${a.type}`);
    }
  }
}

(async () => {
  const args = parseArgs(process.argv.slice(2));
  const base = args.base || DEFAULT_BASE;
  const out = path.resolve(args.out || 'shot.png');
  const viewport = parseViewport(args.viewport);
  const scale = Number(args.scale || 2);
  const settleMs = Number(args.settle || 4000);

  const flowMod = args.flow ? require(path.resolve(args.flow)) : null;
  const lsEntries = Object.assign(
    {},
    (flowMod && flowMod.localStorage) || {},
    loadJsonMaybe(args.ls) || {}
  );

  const browser = await launchBrowser();
  try {
    const page = await browser.newPage();
    await page.setViewport({ ...viewport, deviceScaleFactor: scale });
    const fixtureBase = args['fixture-base'] || (flowMod && flowMod.fixtureBase);
    if (fixtureBase) await forceDemoFixtures(page, fixtureBase);
    await seedLocalStorage(page, lsEntries);
    const url = resolveUrl(base, args.url || (flowMod && flowMod.path) || '/docs/demo/');
    await gotoAndSettle(page, url, { settleMs });
    if (!args['keep-banner']) await suppressDemoBanner(page);

    const needCursor = !!(flowMod || args.actions || args.cursor);
    if (needCursor) await installCursor(page);
    const ctx = makeCtx(page);

    if (flowMod && typeof flowMod.run === 'function') await flowMod.run(ctx);
    if (args.actions) await runActions(ctx, JSON.parse(args.actions));
    if (!args['keep-banner']) await suppressDemoBanner(page); // late toasts

    if (!args.cursor && needCursor) {
      await page.evaluate(() => {
        const c = document.getElementById('__cap_cursor__');
        if (c) c.remove();
      });
    }
    if (args.wait) await new Promise((r) => setTimeout(r, Number(args.wait)));

    fs.mkdirSync(path.dirname(out), { recursive: true });
    await page.screenshot({ path: out, fullPage: !!args['full-page'] });
    console.log(`[shot] wrote ${out} (${viewport.width}x${viewport.height} @${scale}x from ${url})`);
  } finally {
    await browser.close();
  }
})().catch((err) => {
  console.error('[shot] FAILED:', err.message);
  process.exit(1);
});
