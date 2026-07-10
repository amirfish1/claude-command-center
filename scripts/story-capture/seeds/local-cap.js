#!/usr/bin/env node
// local-cap.js — element-clip capture helper for live-server product-story shots.
//
// The shared shot.js captures the whole viewport; this thin wrapper adds
// `--clip <selector>` (crop to one element's bounding box + padding) so focused
// feature stills (queue board, health strip, usage popover, handoff modal) come
// out tight. It REUSES the sibling harness engine (lib.js) unchanged.
//
// Usage:
//   node local-cap.js --base http://127.0.0.1:8091 --url / \
//     --ls seeds/local-live.json --flow flows/local-foo.js \
//     --clip '#queueHealthStrip' --pad 14 --out out.png [--scale 2]
//     [--viewport 1440x900] [--settle 6000] [--wait 1200] [--boxes 'a,b,c']
//
// --boxes prints bounding boxes for a comma-separated selector list and exits
// (no screenshot) — handy for discovering the right clip target.
'use strict';
const path = require('path');
const fs = require('fs');
const HARNESS = path.join(__dirname, '..');
const {
  launchBrowser, seedLocalStorage, gotoAndSettle, suppressDemoBanner,
  installCursor, makeCtx, parseViewport, parseArgs, loadJsonMaybe, resolveUrl,
} = require(path.join(HARNESS, 'lib.js'));

async function runActions(ctx, actions) {
  for (const a of actions) {
    switch (a.type) {
      case 'wait': await ctx.pause(a.ms || 500); break;
      case 'waitFor': await ctx.waitFor(a.selector, a.timeout); break;
      case 'click': await ctx.click(a.selector, a); break;
      case 'move': await ctx.move(a.selector || { x: a.x, y: a.y }, a); break;
      case 'scroll': await ctx.scrollEl(a.selector || null, a.dy || 400, a); break;
      case 'type': await ctx.type(a.selector, a.text, a); break;
      case 'eval': await ctx.eval(new Function(a.code)); break;
      default: throw new Error(`unknown action: ${a.type}`);
    }
  }
}

(async () => {
  const args = parseArgs(process.argv.slice(2));
  const base = args.base || process.env.CAP_BASE || 'http://127.0.0.1:8091';
  const viewport = parseViewport(args.viewport);
  const scale = Number(args.scale || 2);
  const settleMs = Number(args.settle || 6000);
  const pad = Number(args.pad ?? 12);
  const flowMod = args.flow ? require(path.resolve(args.flow)) : null;
  const lsEntries = Object.assign({}, (flowMod && flowMod.localStorage) || {},
    loadJsonMaybe(args.ls) || {});

  const browser = await launchBrowser();
  try {
    const page = await browser.newPage();
    await page.setViewport({ ...viewport, deviceScaleFactor: scale });
    await seedLocalStorage(page, lsEntries);
    const url = resolveUrl(base, args.url || (flowMod && flowMod.path) || '/');
    await gotoAndSettle(page, url, { settleMs });
    await suppressDemoBanner(page).catch(() => {});
    const needCursor = !!(flowMod || args.actions);
    if (needCursor) await installCursor(page);
    const ctx = makeCtx(page);
    if (flowMod && typeof flowMod.run === 'function') await flowMod.run(ctx);
    if (args.actions) await runActions(ctx, JSON.parse(args.actions));

    if (args.boxes) {
      const sels = String(args.boxes).split(',').map((s) => s.trim());
      const out = await page.evaluate((ss) => ss.map((sel) => {
        const el = document.querySelector(sel);
        if (!el) return { sel, found: false };
        const r = el.getBoundingClientRect();
        return { sel, found: true, x: Math.round(r.left), y: Math.round(r.top),
                 w: Math.round(r.width), h: Math.round(r.height) };
      }), sels);
      console.log(JSON.stringify(out, null, 2));
      return;
    }

    if (!args.cursor && needCursor) {
      await page.evaluate(() => { const c = document.getElementById('__cap_cursor__'); if (c) c.remove(); });
    }
    if (args.wait) await new Promise((r) => setTimeout(r, Number(args.wait)));

    const out = path.resolve(args.out || 'local-cap.png');
    fs.mkdirSync(path.dirname(out), { recursive: true });
    let clip = null;
    if (args.clip) {
      const box = await page.evaluate((sel) => {
        const el = document.querySelector(sel);
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { x: r.left, y: r.top, w: r.width, h: r.height };
      }, args.clip);
      if (!box) throw new Error(`--clip selector not found: ${args.clip}`);
      const vw = viewport.width, vh = viewport.height;
      const x = Math.max(0, box.x - pad);
      const y = Math.max(0, box.y - pad);
      clip = {
        x, y,
        width: Math.min(vw - x, box.w + pad * 2),
        height: Math.min(vh - y, box.h + pad * 2),
      };
    }
    await page.screenshot(clip ? { path: out, clip } : { path: out, fullPage: !!args['full-page'] });
    console.log(`[local-cap] wrote ${out}` + (clip
      ? ` clip=${Math.round(clip.width)}x${Math.round(clip.height)} @${scale}x`
      : ` ${viewport.width}x${viewport.height} @${scale}x`) + ` from ${url}`);
  } finally {
    await browser.close();
  }
})().catch((err) => { console.error('[local-cap] FAILED:', err.message); process.exit(1); });
