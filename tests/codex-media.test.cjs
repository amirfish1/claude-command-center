const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer');

const mediaPath = path.resolve('static/codex-media.js');
let browser;

test.before(async () => { browser = await puppeteer.launch({ headless: true }); });
test.after(async () => { await browser?.close(); });

async function mediaPage(setup = () => {}) {
  assert.equal(fs.existsSync(mediaPath), true, 'the Codex media module must exist');
  const page = await browser.newPage();
  await page.setContent('<main id="workspace"></main>');
  await page.evaluate(setup);
  await page.addScriptTag({ path: mediaPath });
  return page;
}

function terminalCatalog(includeProcess = false) {
  const methods = [
    'command/exec', 'command/exec/write', 'command/exec/resize',
    'command/exec/terminate',
  ];
  if (includeProcess) methods.push(
    'process/spawn', 'process/writeStdin', 'process/resizePty', 'process/kill',
  );
  return {
    server_platform: 'linux',
    methods: methods.map(method => ({ method, available: true })),
    notifications: [],
  };
}

function realtimeCatalog() {
  return {
    methods: [
      'thread/realtime/listVoices', 'thread/realtime/start',
      'thread/realtime/appendAudio', 'thread/realtime/appendText',
      'thread/realtime/appendSpeech', 'thread/realtime/stop',
    ].map(method => ({ method, available: true })),
    notifications: [],
  };
}

test('command terminal is interactive while exec is pending and isolates decoded output', async () => {
  const page = await mediaPage(() => {
    window.__calls = [];
    window.__errors = [];
    window.__resizeObservers = [];
    window.ResizeObserver = class {
      constructor(callback) { this.callback = callback; window.__resizeObservers.push(this); }
      observe() {}
      disconnect() { this.disconnected = true; }
    };
  });
  try {
    const processId = await page.evaluate(catalog => {
      let settleExec;
      window.__settleExec = value => settleExec(value);
      window.__controller = window.CCCCodexMedia.attach({
        root: document.querySelector('#workspace'),
        context: { threadId: 'thread-1', repoPath: '/workspace' },
        catalog,
        operation(method, params) {
          window.__calls.push({ method, params });
          if (method === 'command/exec') return new Promise(resolve => { settleExec = resolve; });
          return Promise.resolve({});
        },
        error(message) { window.__errors.push(message); },
      });
      document.querySelector('[data-codex-command]').value = 'printf hello';
      document.querySelector('[data-codex-terminal-form]').requestSubmit();
      return window.__calls[0].params.processId;
    }, terminalCatalog());

    await page.waitForFunction(() => document.querySelector('[data-codex-terminal-running]').hidden === false);
    const started = await page.evaluate(() => ({ call: window.__calls[0], running: document.querySelector('[data-codex-terminal-running]').hidden }));
    assert.equal(started.running, false);
    assert.equal(started.call.method, 'command/exec');
    assert.deepEqual(started.call.params.command, ['printf', 'hello']);
    assert.equal(started.call.params.cwd, '/workspace');
    assert.equal(started.call.params.processId, processId);
    assert.equal(started.call.params.streamStdoutStderr, true);
    assert.equal(started.call.params.streamStdin, true);
    assert.equal(started.call.params.tty, true);
    assert.ok(started.call.params.timeoutMs > 0);
    assert.ok(started.call.params.outputBytesCap > 0);

    const good = Buffer.from('héllo\n').toString('base64');
    const wrong = Buffer.from('wrong terminal\n').toString('base64');
    await page.evaluate(({ processId, good, wrong }) => {
      window.__controller.handleEvents([
        { method: 'command/exec/outputDelta', params: { processId: 'someone-else', stream: 'stdout', deltaBase64: wrong, capReached: false } },
        { method: 'command/exec/outputDelta', params: { processId, stream: 'stdout', deltaBase64: good, capReached: false } },
      ]);
      document.querySelector('[data-codex-stdin]').value = 'ping\n';
      document.querySelector('[data-codex-stdin-send]').click();
      document.querySelector('[data-codex-stdin-close]').click();
      window.__resizeObservers[0].callback([{ contentRect: { width: 800, height: 320 } }]);
      document.querySelector('[data-codex-terminal-stop]').click();
    }, { processId, good, wrong });
    await new Promise(resolve => setTimeout(resolve, 220));

    const active = await page.evaluate(() => ({
      output: document.querySelector('[data-codex-terminal-output]').textContent,
      calls: window.__calls,
    }));
    assert.match(active.output, /héllo/);
    assert.doesNotMatch(active.output, /wrong terminal/);
    assert.deepEqual(active.calls[1], {
      method: 'command/exec/write',
      params: { processId, deltaBase64: Buffer.from('ping\n').toString('base64'), closeStdin: false },
    });
    assert.deepEqual(active.calls[2], {
      method: 'command/exec/write', params: { processId, closeStdin: true },
    });
    const resize = active.calls.find(call => call.method === 'command/exec/resize');
    assert.equal(resize.params.processId, processId);
    assert.ok(Number.isInteger(resize.params.size.cols) && resize.params.size.cols >= 20 && resize.params.size.cols <= 500);
    assert.ok(Number.isInteger(resize.params.size.rows) && resize.params.size.rows >= 2 && resize.params.size.rows <= 200);
    assert.ok(active.calls.some(call => call.method === 'command/exec/terminate' && call.params.processId === processId));

    await page.evaluate(() => window.__settleExec({ exitCode: 0, stdout: '', stderr: '' }));
    await page.waitForFunction(() => /Exited 0/.test(document.querySelector('[data-codex-terminal-status]').textContent));
  } finally { await page.close(); }
});

test('experimental host processes use process handles and matching events', async () => {
  const page = await mediaPage(() => { window.__calls = []; window.ResizeObserver = class { observe() {} disconnect() {} }; });
  try {
    const result = await page.evaluate(async catalog => {
      const controller = window.CCCCodexMedia.attach({
        root: document.querySelector('#workspace'), context: { threadId: 't', repoPath: '/workspace' }, catalog,
        operation(method, params) { window.__calls.push({ method, params }); return Promise.resolve({}); }, error() {},
      });
      document.querySelector('[data-codex-terminal-backend]').value = 'process';
      document.querySelector('[data-codex-command]').value = 'echo host';
      document.querySelector('[data-codex-terminal-form]').requestSubmit();
      await Promise.resolve();
      const handle = window.__calls[0].params.processHandle;
      controller.handleEvents([
        { method: 'process/outputDelta', params: { processHandle: 'other', stream: 'stdout', deltaBase64: btoa('no'), capReached: false } },
        { method: 'process/outputDelta', params: { processHandle: handle, stream: 'stdout', deltaBase64: btoa('yes'), capReached: false } },
      ]);
      const output = document.querySelector('[data-codex-terminal-output]').textContent;
      controller.dispose();
      await Promise.resolve();
      return { calls: window.__calls, output, mounted: !!document.querySelector('.codex-media') };
    }, terminalCatalog(true));
    assert.equal(result.calls[0].method, 'process/spawn');
    assert.ok(result.calls[0].params.processHandle);
    assert.equal(result.calls[0].params.cwd, '/workspace');
    assert.deepEqual(result.calls.at(-1), { method: 'process/kill', params: { processHandle: result.calls[0].params.processHandle } });
    assert.equal(result.output, 'yes');
    assert.equal(result.mounted, false);
  } finally { await page.close(); }
});

test('microphone PCM is resampled to bounded serial 24 kHz mono chunks', async () => {
  const page = await mediaPage(() => {
    window.__calls = [];
    window.__errors = [];
    window.__track = { stopped: false, stop() { this.stopped = true; } };
    navigator.mediaDevices = { getUserMedia: async () => ({ getTracks: () => [window.__track] }) };
    window.AudioContext = class {
      constructor() { this.sampleRate = 48000; this.currentTime = 10; this.destination = {}; window.__context = this; window.__playStarts = []; }
      createMediaStreamSource() { return { connect(node) { window.__processor = node; }, disconnect() {} }; }
      createScriptProcessor() { return { onaudioprocess: null, connect() {}, disconnect() {} }; }
      createBuffer() { return { copyToChannel() {} }; }
      createBufferSource() { return { connect() {}, start(when) { window.__playStarts.push(when); }, stop() {} }; }
      resume() { return Promise.resolve(); }
      close() { this.closed = true; return Promise.resolve(); }
    };
  });
  try {
    await page.evaluate(catalog => {
      let releaseFirst;
      window.__releaseFirst = () => releaseFirst();
      let uploads = 0;
      window.__controller = window.CCCCodexMedia.attach({
        root: document.querySelector('#workspace'), context: { threadId: 'thread-audio', repoPath: '/workspace' }, catalog,
        operation(method, params) {
          window.__calls.push({ method, params });
          if (method === 'thread/realtime/listVoices') return Promise.resolve({ voices: { defaultV1: 'alloy', defaultV2: 'marin', v1: ['alloy'], v2: ['marin', 'cedar'] } });
          if (method === 'thread/realtime/appendAudio' && uploads++ === 0) return new Promise(resolve => { releaseFirst = resolve; });
          return Promise.resolve({});
        },
        error(message) { window.__errors.push(message); },
      });
    }, realtimeCatalog());
    await page.waitForFunction(() => document.querySelector('[data-codex-voice] option'));
    await page.click('[data-codex-realtime-start]');
    await page.waitForFunction(() => window.__processor && typeof window.__processor.onaudioprocess === 'function');
    await page.evaluate(() => {
      window.__controller.handleEvents([
        { method: 'thread/realtime/item/transcript/delta', params: { threadId: 'thread-audio', itemId: 'item-1', delta: 'Hello' } },
        { method: 'thread/realtime/transcript/done', params: { threadId: 'thread-audio', role: 'assistant', text: 'Hello there' } },
        { method: 'thread/realtime/outputAudio/delta', params: { threadId: 'thread-audio', audio: { data: btoa('\u0000\u0000'), numChannels: 1, sampleRate: 24000, samplesPerChannel: 1 } } },
        { method: 'thread/realtime/outputAudio/delta', params: { threadId: 'thread-audio', audio: { data: btoa('\u0000\u0000'), numChannels: 1, sampleRate: 24000, samplesPerChannel: 1 } } },
      ]);
      const samples = new Float32Array(480).fill(0.5);
      const event = { inputBuffer: { numberOfChannels: 1, getChannelData: () => samples } };
      for (let index = 0; index < 14; index++) window.__processor.onaudioprocess(event);
    });
    await page.waitForFunction(() => window.__calls.some(call => call.method === 'thread/realtime/appendAudio'));
    const first = await page.evaluate(() => window.__calls.find(call => call.method === 'thread/realtime/appendAudio'));
    assert.equal(first.params.threadId, 'thread-audio');
    assert.equal(first.params.audio.sampleRate, 24000);
    assert.equal(first.params.audio.numChannels, 1);
    assert.equal(first.params.audio.samplesPerChannel, 240);
    const realtimeView = await page.evaluate(() => ({
      transcript: document.querySelector('[data-codex-realtime-transcript]').textContent,
      playStarts: window.__playStarts,
      start: window.__calls.find(call => call.method === 'thread/realtime/start'),
    }));
    assert.match(realtimeView.transcript, /Hello/);
    assert.match(realtimeView.transcript, /assistant: Hello there/);
    assert.equal(realtimeView.playStarts[0], 10);
    assert.ok(realtimeView.playStarts[1] > realtimeView.playStarts[0]);
    assert.deepEqual(realtimeView.start.params, { threadId: 'thread-audio', outputModality: 'audio', voice: 'marin' });
    const pcm = Buffer.from(first.params.audio.data, 'base64');
    assert.equal(pcm.length, 480);
    assert.ok(Math.abs(pcm.readInt16LE(0) - 16383) <= 1);

    await page.evaluate(() => window.__releaseFirst({}));
    await page.waitForFunction(() => document.querySelector('[data-codex-realtime-status]').textContent.includes('dropped'));
    await new Promise(resolve => setTimeout(resolve, 80));
    const uploads = await page.evaluate(() => window.__calls.filter(call => call.method === 'thread/realtime/appendAudio').length);
    assert.ok(uploads > 1 && uploads < 14, `expected a bounded queue, got ${uploads} uploads`);
  } finally { await page.close(); }
});

test('dispose stops native realtime and releases a late microphone acquisition', async () => {
  const page = await mediaPage(() => {
    window.__calls = [];
    window.__errors = [];
    window.__tracks = [];
    let resolveMic;
    window.__resolveMic = () => {
      const track = { stopped: false, stop() { this.stopped = true; } };
      window.__tracks.push(track);
      resolveMic({ getTracks: () => [track] });
    };
    navigator.mediaDevices = { getUserMedia: () => new Promise(resolve => { resolveMic = resolve; }) };
    window.AudioContext = class { constructor() { this.sampleRate = 48000; } close() { return Promise.resolve(); } };
  });
  try {
    await page.evaluate(catalog => {
      window.__controller = window.CCCCodexMedia.attach({
        root: document.querySelector('#workspace'), context: { threadId: 'late-thread', repoPath: '/workspace' }, catalog,
        operation(method, params) {
          window.__calls.push({ method, params });
          if (method === 'thread/realtime/listVoices') return Promise.resolve({ voices: { defaultV1: 'alloy', defaultV2: 'marin', v1: ['alloy'], v2: ['marin'] } });
          return Promise.resolve({});
        }, error(message) { window.__errors.push(message); },
      });
    }, realtimeCatalog());
    await page.click('[data-codex-realtime-start]');
    await page.waitForFunction(() => window.__calls.some(call => call.method === 'thread/realtime/start'));
    await page.evaluate(() => window.__controller.dispose());
    await page.evaluate(() => window.__resolveMic());
    await page.waitForFunction(() => window.__tracks[0]?.stopped === true);
    assert.equal(await page.evaluate(() => window.__tracks[0].stopped), true);
    assert.equal(await page.evaluate(() => window.__calls.some(call => call.method === 'thread/realtime/stop')), true);
    assert.equal(await page.$('.codex-media'), null);
  } finally { await page.close(); }
});

test('a realtime error releases active capture and remains visible', async () => {
  const page = await mediaPage(() => {
    window.__calls = [];
    window.__errors = [];
    window.__track = { stopped: false, stop() { this.stopped = true; } };
    navigator.mediaDevices = { getUserMedia: async () => ({ getTracks: () => [window.__track] }) };
    window.AudioContext = class {
      constructor() { this.sampleRate = 48000; this.destination = {}; window.__context = this; }
      createMediaStreamSource() { return { connect(node) { window.__processor = node; }, disconnect() {} }; }
      createScriptProcessor() { return { connect() {}, disconnect() {}, onaudioprocess: null }; }
      resume() { return Promise.resolve(); }
      close() { this.closed = true; return Promise.resolve(); }
    };
  });
  try {
    await page.evaluate(catalog => {
      window.__controller = window.CCCCodexMedia.attach({
        root: document.querySelector('#workspace'), context: { threadId: 'error-thread', repoPath: '/workspace' }, catalog,
        operation(method, params) {
          window.__calls.push({ method, params });
          if (method === 'thread/realtime/listVoices') return Promise.resolve({ voices: { defaultV1: 'alloy', defaultV2: 'marin', v1: ['alloy'], v2: ['marin'] } });
          return Promise.resolve({});
        }, error(message) { window.__errors.push(message); },
      });
    }, realtimeCatalog());
    await page.click('[data-codex-realtime-start]');
    await page.waitForFunction(() => window.__processor && window.__track.stopped === false);
    await page.evaluate(() => window.__controller.handleEvents([
      { method: 'thread/realtime/error', params: { threadId: 'other-thread', message: 'ignore me' } },
      { method: 'thread/realtime/error', params: { threadId: 'error-thread', message: 'Native realtime unavailable' } },
    ]));
    const errored = await page.evaluate(() => ({
      errors: window.__errors,
      stopped: window.__track.stopped,
      closed: window.__context.closed,
      status: document.querySelector('[data-codex-realtime-status]').textContent,
    }));
    assert.equal(errored.stopped, true);
    assert.equal(errored.closed, true);
    assert.match(errored.status, /Native realtime unavailable/);
    assert.deepEqual(errored.errors, ['Native realtime unavailable']);
  } finally { await page.close(); }
});
