const {test} = require('node:test');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');

test('default-on, audio-thread playback through page stalls, pause, cancellation and disabling', {timeout: 30000}, async () => {
  const server = spawn('python3', ['tests/fixtures/browser_voice_server.py'], {
    env: {...process.env, CCC_BROWSER_VOICE_TEST_WORKER: '1'}, stdio: ['ignore', 'pipe', 'pipe'],
  });
  let browser;
  try {
    const [chunk] = await once(server.stdout, 'data');
    const url = 'http://127.0.0.1:' + String(chunk).trim();
    browser = await puppeteer.launch({executablePath: findChromePath(), args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required']});
    const page = await browser.newPage();
    await page.evaluateOnNewDocument(() => {
      const Original = window.AudioContext;
      window.audioContextStarts = [];
      window.AudioContext = class extends Original {
        constructor(...args) { super(...args); window.audioContextStarts.push(performance.now()); }
      };
    });
    const requests = [], errors = [];
    page.on('request', r => requests.push(r.url()));
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(url, {waitUntil: 'load'});
    assert.equal(await page.evaluate(() => cccBrowserVoice.enabled), true);
    await page.waitForFunction(() => cccBrowserVoice.ready);
    // A child conversation can finish loading its scripts before its real
    // transcript is painted. Do not initialize a second audio stack then.
    await page.evaluate(async () => {
      const frame = document.createElement('iframe');
      frame.id = 'voiceEmbed'; frame.src = '/?conv=test-session&embed=app';
      const loaded = new Promise(resolve => { frame.onload = resolve; });
      document.body.appendChild(frame); await loaded;
    });
    const frame = await (await page.$('#voiceEmbed')).contentFrame();
    assert.equal(await frame.evaluate(() => cccBrowserVoice.enabled), true);
    assert.equal(await frame.evaluate(() => audioContextStarts.length), 0);
    await frame.evaluate(() => {
      window.conversationPaintedAt = performance.now();
      window.dispatchEvent(new Event('ccc:conversation-drawn'));
    });
    await frame.waitForFunction(() => cccBrowserVoice.ready);
    assert.ok(await frame.evaluate(() => audioContextStarts.length === 1
      && audioContextStarts[0] >= conversationPaintedAt));
    await page.evaluate(() => document.getElementById('voiceEmbed').remove());
    await page.evaluate(() => {
      window.events = [];
      window.handle = cccBrowserVoice.start('A synthetic six-second transport test.', {rate: 1.25,
        onstart: d => events.push({kind: 'start', ...d}),
        onposition: d => { window.latest = d; },
        ontelemetry: d => events.push({kind: 'telemetry', ...d}),
        onerror: e => events.push({kind: 'error', message: e.message}),
        onend: () => events.push({kind: 'end'}),
      });
    });
    await page.waitForFunction(() => window.latest?.played > 0.3);
    const stalled = await page.evaluate(async () => {
      const before = latest.played;
      const start = performance.now();
      while (performance.now() - start < 900) {} // An actual blocked page thread.
      await new Promise(resolve => setTimeout(resolve, 50));
      return {before, after: latest.played, gap: latest.maxGapMs, buffer: latest.maxBufferedMs};
    });
    assert.ok(stalled.after - stalled.before > 0.8, JSON.stringify(stalled));
    assert.ok(stalled.gap < 30, 'the audio worker/worklet, not the blocked page, advances playback');
    assert.ok(stalled.buffer <= 1000);
    await page.evaluate(() => handle.pause());
    const paused = await page.evaluate(async () => {
      await new Promise(r => setTimeout(r, 100));
      const before = latest.played;
      await new Promise(r => setTimeout(r, 250));
      return {before, after: latest.played};
    });
    assert.equal(paused.after, paused.before);
    await page.evaluate(() => handle.resume());
    await page.waitForFunction(before => latest.played > before + 0.1, {}, paused.after);
    await page.evaluate(() => handle.cancel());
    const count = await page.evaluate(() => events.length);
    await page.evaluate(() => {
      window.handle = cccBrowserVoice.start('New read after cancellation.', {onstart: () => events.push({kind: 'second-start'})});
    });
    await page.waitForFunction(() => events.some(e => e.kind === 'second-start'));
    await page.click('#settingsBrowserVoiceToggle');
    assert.equal(await page.evaluate(() => cccBrowserVoice.enabled), false);
    assert.equal(await page.evaluate(() => cccBrowserVoice.ready), false);
    assert.equal(await page.evaluate(() => localStorage.getItem('ccc-browser-voice')), '0');
    assert.equal(await page.evaluate(n => events.slice(n).some(e => e.kind === 'end'), count), false, 'a cancelled read never reports completion');
    await page.click('#settingsBrowserVoiceToggle');
    await page.waitForFunction(() => cccBrowserVoice.ready);
    await page.evaluate(() => {
      window.slowError = null;
      cccBrowserVoice.start('A slow producer must stop browser voice.', {rate: 1.25,
        onerror: e => { slowError = e.message; }, onend: () => { slowError = 'incorrect success'; }});
    });
    await page.waitForFunction(() => window.slowError !== null);
    assert.match(await page.evaluate(() => slowError), /cannot sustain playback/);
    assert.equal(await page.evaluate(() => cccBrowserVoice.enabled), false);
    assert.equal(await page.evaluate(() => localStorage.getItem('ccc-browser-voice')), '1',
      'a playback failure must preserve the user preference');
    await page.reload({waitUntil: 'load'});
    await page.waitForFunction(() => cccBrowserVoice.ready);
    assert.equal(await page.evaluate(() => cccBrowserVoice.enabled), true,
      'a new page retries after a transient failure');
    assert.deepEqual(errors, []);
  } finally {
    await browser?.close();
    server.kill('SIGTERM');
  }
});
