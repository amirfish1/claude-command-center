#!/usr/bin/env node
// Video tool for the product-story capture harness.
//
// Records a cursor-led clip of a scripted flow against CCC (demo bundle or
// live server). A synthetic ~20px pointer with a soft shadow is injected and
// animated in sync with the real page.mouse events; clicks get a ripple.
//
// Recording pipeline: page.screencast() → WebM (VP9). If ffmpeg is on PATH
// the WebM is transcoded to MP4 (H.264, yuv420p, +faststart) and a poster
// PNG is extracted. Without ffmpeg, puppeteer's screencast itself fails
// (it shells out to ffmpeg), so we detect up front and bail with a clear
// message — the still-image fallback is shot.js.
//
// Usage:
//   node scripts/story-capture/record.js --flow flows/kanban-drag.js \
//     --out docs/product-story/assets/video/V-06-kanban-drag.mp4 \
//     [--poster docs/product-story/assets/video/posters/V-06.png]
//     [--viewport 1440x900]   1x for video; use shot.js @2x for stills
//     [--base http://127.0.0.1:8877]   or env CAP_BASE
//     [--ls seed.json]        extra localStorage entries (merged over flow's)
//     [--fps 30] [--crf 20]
//     [--lead 1500] [--tail 1500]   establish / settle ms around the flow
//     [--keep-webm]           keep the intermediate WebM next to the MP4
//     [--poster-at 1.0]       poster timestamp in seconds
//
// Flow modules export: { path, viewport?, localStorage?, lead?, tail?,
//                        run: async (ctx) => {...} }
// ctx helpers: move, click, dragHTML5, scrollEl, type, pause, waitFor, eval.
//
// Exit codes: 0 ok; 1 error.
'use strict';

const path = require('path');
const fs = require('fs');
const { spawnSync } = require('child_process');
const {
  DEFAULT_BASE, launchBrowser, forceDemoFixtures, seedLocalStorage,
  gotoAndSettle, suppressDemoBanner, installCursor, makeCtx, parseViewport,
  parseArgs, loadJsonMaybe, resolveUrl,
} = require('./lib.js');

function ffmpegAvailable() {
  const r = spawnSync('ffmpeg', ['-version'], { stdio: 'ignore' });
  return r.status === 0;
}

function run(cmd, argv) {
  const r = spawnSync(cmd, argv, { encoding: 'utf8' });
  if (r.status !== 0) {
    throw new Error(`${cmd} ${argv.join(' ')} failed (${r.status}): ${r.stderr && r.stderr.slice(-800)}`);
  }
  return r.stdout;
}

(async () => {
  const args = parseArgs(process.argv.slice(2));
  if (!args.flow) throw new Error('--flow <module.js> is required');
  const flow = require(path.resolve(args.flow));
  if (typeof flow.run !== 'function') throw new Error('flow module must export run(ctx)');

  const base = args.base || DEFAULT_BASE;
  const url = resolveUrl(base, args.url || flow.path || '/docs/demo/');
  const out = path.resolve(args.out || 'recording.mp4');
  const viewport = parseViewport(args.viewport || flow.viewport, { width: 1440, height: 900 });
  const fps = Number(args.fps || 30);
  const crf = String(args.crf || 20);
  const lead = Number(args.lead ?? flow.lead ?? 1500);
  const tail = Number(args.tail ?? flow.tail ?? 1500);
  const posterAt = String(args['poster-at'] || '1.0');

  const hasFfmpeg = ffmpegAvailable();
  if (!hasFfmpeg) {
    // puppeteer's screencast also requires ffmpeg — no WebM without it either.
    throw new Error('ffmpeg not found on PATH; page.screencast() cannot encode. '
      + 'Install ffmpeg or capture stills with shot.js instead.');
  }
  const wantMp4 = out.endsWith('.mp4');
  const webmPath = wantMp4 ? out.replace(/\.mp4$/, '.tmp.webm') : out;
  fs.mkdirSync(path.dirname(out), { recursive: true });

  const lsEntries = Object.assign({}, flow.localStorage || {}, loadJsonMaybe(args.ls) || {});

  const browser = await launchBrowser();
  try {
    const page = await browser.newPage();
    await page.setViewport({ ...viewport, deviceScaleFactor: 1 });
    // Dark default background: kills the white flash on in-flow reloads.
    const cdp = await page.createCDPSession();
    await cdp.send('Emulation.setDefaultBackgroundColorOverride',
      { color: { r: 13, g: 17, b: 23, a: 255 } }).catch(() => {});
    const fixtureBase = args['fixture-base'] || flow.fixtureBase;
    if (fixtureBase) await forceDemoFixtures(page, fixtureBase);
    await seedLocalStorage(page, lsEntries);
    await gotoAndSettle(page, url);
    await suppressDemoBanner(page);
    await installCursor(page, viewport.width * 0.45, viewport.height * 0.55);
    const ctx = makeCtx(page);

    console.log(`[record] recording ${url} at ${viewport.width}x${viewport.height} @${fps}fps`);
    const recorder = await page.screencast({ path: webmPath, fps });
    await ctx.pause(lead);        // establishing shot
    await flow.run(ctx);
    await ctx.pause(tail);        // let the end state read
    await recorder.stop();
    console.log(`[record] wrote ${webmPath}`);
  } finally {
    await browser.close();
  }

  if (wantMp4) {
    // H.264 + yuv420p + faststart: plays everywhere (Safari, QuickTime, docs
    // sites). Even-dimension clamp guards odd viewports.
    run('ffmpeg', [
      '-y', '-i', webmPath,
      '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
      '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', crf,
      '-movflags', '+faststart', '-an',
      out,
    ]);
    if (!args['keep-webm']) fs.unlinkSync(webmPath);
    console.log(`[record] wrote ${out}`);
  }

  const posterOut = args.poster
    ? path.resolve(args.poster)
    : path.join(path.dirname(out), 'posters',
        path.basename(out).replace(/\.(mp4|webm)$/, '') + '.png');
  fs.mkdirSync(path.dirname(posterOut), { recursive: true });
  run('ffmpeg', ['-y', '-ss', posterAt, '-i', out, '-frames:v', '1', posterOut]);
  console.log(`[record] poster ${posterOut}`);

  const probe = run('ffprobe', [
    '-v', 'error', '-select_streams', 'v:0',
    '-show_entries', 'stream=width,height,codec_name:format=duration,size',
    '-of', 'default=noprint_wrappers=1', out,
  ]);
  console.log('[record] ffprobe:\n' + probe.trim());
})().catch((err) => {
  console.error('[record] FAILED:', err.message);
  process.exit(1);
});
