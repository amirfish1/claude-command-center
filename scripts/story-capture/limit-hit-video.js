#!/usr/bin/env node
// B20: build the 20 s "Your Claude limit hits. Your 12 sessions keep going."
// demo from flows/limit-hit.js — 16:9 + 9:16 MP4s with burned-in captions
// and an end card, plus a README GIF. No audio, no TTS.
//
// Serve the repo root first (see README.md in this folder), then:
//   node scripts/story-capture/limit-hit-video.js [--out-dir DIR] [--skip-record]
//
// Outputs (default dirs are the repo's asset folders):
//   docs/product-story/assets/video/V-20-limit-hit-16x9.mp4   1920x1080
//   docs/product-story/assets/video/V-20-limit-hit-9x16.mp4   1080x1920
//   docs/images/feature-wall/limit-hit.gif                      720px, 12 fps
//
// Captions follow the clip: the flow writes act timestamps (LIMIT_HIT_MARKS)
// and the caption track is built from them, so a slow VM can't drift them.
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');
const { parseArgs } = require('./lib.js');

const ROOT = path.resolve(__dirname, '..', '..');
const FLOW = path.join(__dirname, 'flows', 'limit-hit.js');
const TOTAL = 20;      // seconds, final length
const CARD = 4;        // seconds of end card
const LEAD_S = 2.8;    // flows/limit-hit.js lead (ms) / 1000
const BG = '0x0d1117';

const LAYOUTS = {
  landscape: {
    suffix: '16x9', w: 1920, h: 1080,
    caption: { size: 56, marginV: 70 }, sub: 34,
    card: { title: 86, line: 46, url: 34 },
  },
  portrait: {
    suffix: '9x16', w: 1080, h: 1920,
    caption: { size: 62, marginV: 360 }, sub: 38,
    card: { title: 84, line: 52, url: 34 },
  },
};

function run(cmd, argv, env) {
  const r = spawnSync(cmd, argv, { encoding: 'utf8', env: { ...process.env, ...env }, stdio: ['ignore', 'pipe', 'pipe'] });
  if (r.status !== 0) throw new Error(`${cmd} failed (${r.status}): ${(r.stderr || '').slice(-1200)}`);
  return r.stdout;
}

function ts(sec) {
  const s = Math.max(0, sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const cs = Math.round((s % 60) * 100);
  return `${h}:${String(m).padStart(2, '0')}:${String(Math.floor(cs / 100)).padStart(2, '0')}.${String(cs % 100).padStart(2, '0')}`;
}

function assFile(L, marks) {
  const at = (k) => LEAD_S + marks[k] / 1000;
  const main = TOTAL - CARD;
  const fade = '{\\fad(180,180)}';
  const cx = L.w / 2;
  const cy = L.h / 2;
  const lines = [
    [0.4, at('limit'), '12 Claude Code sessions, all working.'],
    [at('limit'), at('click'), 'Your Claude limit hits.'],
    [at('click'), at('free'), 'One click.'],
    [at('free'), main, `Your 12 sessions keep going.\\N{\\fs${L.sub}\\b0}on a free model, through your own router`],
  ];
  const ev = lines.map(([a, b, text]) => `Dialogue: 0,${ts(a)},${ts(b)},Caption,,0,0,0,,${fade}${text}`);
  const c = L.card;
  ev.push(
    `Dialogue: 1,${ts(main)},${ts(TOTAL)},Card,,0,0,0,,{\\an5\\pos(${cx},${cy - c.title * 1.2})\\fs${c.title}\\fad(250,0)}Claude Command Center`,
    `Dialogue: 1,${ts(main + 0.3)},${ts(TOTAL)},Card,,0,0,0,,{\\an5\\pos(${cx},${cy + c.line * 0.6})\\fs${c.line}\\b0\\fad(250,0)}`
      + (L.w < L.h ? 'Your Claude limit hits.\\NYour 12 sessions keep going.' : 'Your Claude limit hits. Your 12 sessions keep going.'),
    `Dialogue: 1,${ts(main + 0.6)},${ts(TOTAL)},Url,,0,0,0,,{\\an5\\pos(${cx},${cy + c.line * (L.w < L.h ? 3.2 : 2.4)})\\fs${c.url}\\fad(250,0)}github.com/amirfish1/claude-command-center`,
  );
  return `[Script Info]
ScriptType: v4.00+
PlayResX: ${L.w}
PlayResY: ${L.h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Inter,${L.caption.size},&H00FFFFFF,&H00FFFFFF,&H28171311,&H00000000,1,0,0,0,100,100,0,0,3,22,0,2,60,60,${L.caption.marginV},1
Style: Card,Inter,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,5,60,60,0,1
Style: Url,Inter,34,&H005777D9,&H005777D9,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
${ev.join('\n')}
`;
}

function build(layout, opts) {
  const L = LAYOUTS[layout];
  const work = opts.work;
  const raw = path.join(work, `raw-${layout}.mp4`);
  const marksPath = path.join(work, `marks-${layout}.json`);
  if (!opts.skipRecord) {
    console.log(`[limit-hit] recording ${layout}...`);
    run('node', [path.join(__dirname, 'record.js'), '--flow', FLOW, '--out', raw,
      '--poster', path.join(work, `poster-${layout}.png`)],
    { LIMIT_HIT_LAYOUT: layout, LIMIT_HIT_MARKS: marksPath });
  }
  const marks = JSON.parse(fs.readFileSync(marksPath, 'utf8'));
  const ass = path.join(work, `captions-${layout}.ass`);
  fs.writeFileSync(ass, assFile(L, marks));
  const out = path.join(opts.videoDir, `V-20-limit-hit-${L.suffix}.mp4`);
  const main = TOTAL - CARD;
  // Clip -> scale to the export size, hold the last frame if short, cut at
  // `main`; append the end card; burn the caption track over both.
  const graph = [
    `[0:v]scale=${L.w}:${L.h}:flags=lanczos,setsar=1,fps=30,tpad=stop_mode=clone:stop_duration=${TOTAL},trim=0:${main},setpts=PTS-STARTPTS[clip]`,
    `color=c=${BG}:s=${L.w}x${L.h}:r=30:d=${CARD},setsar=1[card]`,
    `[clip][card]concat=n=2:v=1:a=0,format=yuv420p,ass='${ass.replace(/'/g, "\\'")}'[v]`,
  ].join(';');
  run('ffmpeg', ['-y', '-v', 'error', '-i', raw, '-filter_complex', graph, '-map', '[v]',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '20', '-pix_fmt', 'yuv420p',
    '-movflags', '+faststart', '-an', out]);
  console.log(`[limit-hit] wrote ${path.relative(ROOT, out)}`);
  return out;
}

function gif(src, out) {
  const pal = path.join(path.dirname(out), '.limit-hit-palette.png');
  const vf = 'fps=12,scale=720:-1:flags=lanczos';
  run('ffmpeg', ['-y', '-v', 'error', '-i', src, '-vf', `${vf},palettegen=stats_mode=diff`, pal]);
  run('ffmpeg', ['-y', '-v', 'error', '-i', src, '-i', pal, '-lavfi',
    `${vf}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle`, out]);
  fs.unlinkSync(pal);
  console.log(`[limit-hit] wrote ${path.relative(ROOT, out)}`);
}

(() => {
  const args = parseArgs(process.argv.slice(2));
  const outDir = args['out-dir'] ? path.resolve(args['out-dir']) : null;
  const opts = {
    skipRecord: !!args['skip-record'],
    work: path.resolve(args.work || path.join(os.tmpdir(), 'ccc-limit-hit')),
    videoDir: outDir || path.join(ROOT, 'docs', 'product-story', 'assets', 'video'),
  };
  const gifOut = path.join(outDir || path.join(ROOT, 'docs', 'images', 'feature-wall'), 'limit-hit.gif');
  fs.mkdirSync(opts.work, { recursive: true });
  fs.mkdirSync(opts.videoDir, { recursive: true });
  const wide = build('landscape', opts);
  build('portrait', opts);
  gif(wide, gifOut);
})();
