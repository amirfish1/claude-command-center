// Remote-rendered browser for the status-rail preview pane.
//
// Spawned on demand by server.py (ccc_server/rail_browser.py). Speaks plain
// HTTP on 127.0.0.1 so the stdlib-only dashboard server can proxy it without
// a websocket dependency. Owns ONE headless Chromium page; the dashboard
// polls /frame for the latest JPEG and POSTs input events to /input.
//
// Lifecycle: writes a state file (port + pid) to
// ~/.claude/command-center/rail-browser.json on listen, removes it on exit,
// and self-exits after IDLE_MS without a request.

'use strict';

const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const puppeteer = require('../../require-puppeteer.js');
const { findChromePath } = require('../../puppeteer-browser-config.js');

const STATE_DIR = path.join(os.homedir(), '.claude', 'command-center');
const STATE_FILE = path.join(STATE_DIR, 'rail-browser.json');
const IDLE_MS = 10 * 60 * 1000;
const FRAME_INTERVAL_MS = 400;
const JPEG_QUALITY = 62;
const MAX_BODY = 64 * 1024;

let browser = null;
let page = null;
let lastFrame = null; // Buffer
let frameSeq = 0;
let lastRequestAt = Date.now();
let shuttingDown = false;
let captureWarned = false;

function touch() {
  lastRequestAt = Date.now();
}

function stateRemove() {
  try { fs.unlinkSync(STATE_FILE); } catch (_) {}
}

async function ensurePage(width, height) {
  const w = Math.max(200, Math.min(1920, Math.round(width || 900)));
  const h = Math.max(200, Math.min(1600, Math.round(height || 700)));
  if (!browser) {
    // --no-sandbox matches every other puppeteer caller in this repo
    // (AppArmor hosts); the helper is loopback-only with an ephemeral profile.
    browser = await puppeteer.launch({
      executablePath: findChromePath() || undefined,
      args: ['--no-sandbox', '--disable-dev-shm-usage', '--mute-audio'],
    });
    browser.on('disconnected', () => { browser = null; page = null; });
  }
  if (!page || page.isClosed()) {
    page = await browser.newPage();
    await page.setViewport({ width: w, height: h });
  } else {
    const vp = page.viewport();
    if (!vp || vp.width !== w || vp.height !== h) {
      await page.setViewport({ width: w, height: h });
    }
  }
  return page;
}

async function captureLoop() {
  while (!shuttingDown) {
    try {
      if (page && !page.isClosed()) {
        const shot = await page.screenshot({ type: 'jpeg', quality: JPEG_QUALITY });
        if (shot && shot.length) {
          lastFrame = shot;
          frameSeq += 1;
        }
      }
    } catch (e) {
      // Navigation races and target crashes are transient; keep looping.
      if (!captureWarned) {
        captureWarned = true;
        process.stderr.write('[rail-browser] capture error: ' + String(e && e.message || e) + '\n');
      }
    }
    await new Promise((r) => setTimeout(r, FRAME_INTERVAL_MS));
  }
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (c) => {
      size += c.length;
      if (size > MAX_BODY) { reject(new Error('body too large')); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => {
      try { resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {}); }
      catch (e) { reject(e); }
    });
    req.on('error', reject);
  });
}

function sendJson(res, status, obj) {
  const body = Buffer.from(JSON.stringify(obj));
  res.writeHead(status, { 'Content-Type': 'application/json', 'Content-Length': body.length });
  res.end(body);
}

async function statusPayload() {
  let title = '';
  let url = '';
  try {
    if (page && !page.isClosed()) { title = await page.title(); url = page.url(); }
  } catch (_) {}
  return { ok: true, ready: Boolean(page && !page.isClosed()), url, title, seq: frameSeq };
}

const server = http.createServer(async (req, res) => {
  touch();
  const u = new URL(req.url, 'http://127.0.0.1');
  const p = u.pathname.replace(/\/+$/, '') || '/';
  try {
    if (req.method === 'GET' && p === '/status') {
      sendJson(res, 200, await statusPayload());
      return;
    }
    if (req.method === 'GET' && p === '/frame') {
      if (!lastFrame) { res.writeHead(204).end(); return; }
      res.writeHead(200, {
        'Content-Type': 'image/jpeg',
        'Content-Length': lastFrame.length,
        'Cache-Control': 'no-store',
        'X-Frame-Seq': String(frameSeq),
      });
      res.end(lastFrame);
      return;
    }
    if (req.method !== 'POST') { sendJson(res, 405, { ok: false, error: 'method' }); return; }
    const body = await readBody(req);

    if (p === '/open' || p === '/nav') {
      const url = String(body.url || '');
      const pg = await ensurePage(body.width, body.height);
      await pg.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch((e) => {
        // Navigation timeouts still leave a rendered page half the time.
        if (!/timeout/i.test(String(e))) throw e;
      });
      sendJson(res, 200, await statusPayload());
      return;
    }
    if (p === '/back' || p === '/forward' || p === '/reload') {
      if (!page || page.isClosed()) { sendJson(res, 409, { ok: false, error: 'no page' }); return; }
      if (p === '/back') await page.goBack({ waitUntil: 'domcontentloaded', timeout: 15000 }).catch(() => {});
      else if (p === '/forward') await page.goForward({ waitUntil: 'domcontentloaded', timeout: 15000 }).catch(() => {});
      else await page.reload({ waitUntil: 'domcontentloaded', timeout: 15000 }).catch(() => {});
      sendJson(res, 200, await statusPayload());
      return;
    }
    if (p === '/resize') {
      await ensurePage(body.width, body.height);
      sendJson(res, 200, await statusPayload());
      return;
    }
    if (p === '/input') {
      if (!page || page.isClosed()) { sendJson(res, 409, { ok: false, error: 'no page' }); return; }
      const kind = String(body.kind || '');
      const x = Number(body.x) || 0;
      const y = Number(body.y) || 0;
      if (kind === 'click') {
        await page.mouse.click(x, y, { button: body.button === 'right' ? 'right' : 'left' });
      } else if (kind === 'move') {
        await page.mouse.move(x, y);
      } else if (kind === 'wheel') {
        await page.mouse.move(x, y);
        await page.mouse.wheel({ deltaX: Number(body.dx) || 0, deltaY: Number(body.dy) || 0 });
      } else if (kind === 'key') {
        const key = String(body.key || '');
        if (key) await page.keyboard.press(key);
      } else if (kind === 'type') {
        await page.keyboard.type(String(body.text || ''));
      } else if (kind === 'keydown' || kind === 'keyup') {
        const key = String(body.key || '');
        if (key) await page.keyboard[kind === 'keydown' ? 'down' : 'up'](key);
      } else {
        sendJson(res, 400, { ok: false, error: 'unknown input kind' });
        return;
      }
      sendJson(res, 200, { ok: true, seq: frameSeq });
      return;
    }
    if (p === '/close') {
      sendJson(res, 200, { ok: true });
      shutdown();
      return;
    }
    sendJson(res, 404, { ok: false, error: 'not found' });
  } catch (e) {
    sendJson(res, 500, { ok: false, error: String(e && e.message || e) });
  }
});

async function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  try { if (browser) await browser.close(); } catch (_) {}
  stateRemove();
  setTimeout(() => process.exit(0), 100).unref();
}

setInterval(() => {
  if (Date.now() - lastRequestAt > IDLE_MS) shutdown();
}, 30000).unref();

process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
process.on('exit', stateRemove);

server.listen(0, '127.0.0.1', () => {
  const port = server.address().port;
  try {
    fs.mkdirSync(STATE_DIR, { recursive: true });
    fs.writeFileSync(STATE_FILE, JSON.stringify({ port, pid: process.pid, at: Date.now() }));
  } catch (_) {}
  captureLoop();
  process.stdout.write('rail-browser ready port=' + port + '\n');
});
