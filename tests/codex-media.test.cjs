const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer');

const mediaPath = path.resolve('static/codex-media.js');
let browser;

test.before(async () => { browser = await puppeteer.launch({ headless: true }); });
test.after(async () => { await browser?.close(); });

async function mediaPage(setup = () => {}, setupArg) {
  assert.equal(fs.existsSync(mediaPath), true, 'the Codex media module must exist');
  const page = await browser.newPage();
  await page.setContent('<main id="workspace"></main>');
  await page.evaluate(setup, setupArg);
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
    const started = await page.evaluate(() => ({ call: window.__calls[0], running: document.querySelector('[data-codex-terminal-running]').hidden, active: window.__controller.isActive() }));
    assert.equal(started.running, false);
    assert.equal(started.active, true);
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
    assert.equal(await page.evaluate(() => window.__controller.isActive()), false);
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
      let resolveStop;
      window.__resolveStop = () => resolveStop({});
      window.__controller = window.CCCCodexMedia.attach({
        root: document.querySelector('#workspace'), context: { threadId: 'late-thread', repoPath: '/workspace' }, catalog,
        operation(method, params) {
          window.__calls.push({ method, params });
          if (method === 'thread/realtime/listVoices') return Promise.resolve({ voices: { defaultV1: 'alloy', defaultV2: 'marin', v1: ['alloy'], v2: ['marin'] } });
          if (method === 'thread/realtime/stop') return new Promise(resolve => { resolveStop = resolve; });
          return Promise.resolve({});
        }, error(message) { window.__errors.push(message); },
      });
    }, realtimeCatalog());
    await page.click('[data-codex-realtime-start]');
    await page.waitForFunction(() => window.__calls.some(call => call.method === 'thread/realtime/start'));
    await page.evaluate(() => {
      const pending = window.__controller.dispose();
      window.__disposeIsPromise = !!pending && typeof pending.then === 'function';
      window.__disposeDone = false;
      Promise.resolve(pending).then(() => { window.__disposeDone = true; });
    });
    await page.evaluate(() => window.__resolveMic());
    await page.waitForFunction(() => window.__tracks[0]?.stopped === true);
    assert.equal(await page.evaluate(() => window.__tracks[0].stopped), true);
    assert.equal(await page.evaluate(() => window.__calls.some(call => call.method === 'thread/realtime/stop')), true);
    assert.equal(await page.$('.codex-media'), null);
    assert.deepEqual(await page.evaluate(() => ({ isPromise: window.__disposeIsPromise, done: window.__disposeDone })), { isPromise: true, done: false });
    await page.evaluate(() => window.__resolveStop());
    await page.evaluate(() => window.__controller.handleEvents([
      { seq: 1, method: 'thread/realtime/started', params: { threadId: 'late-thread', version: 'v2', realtimeSessionId: null } },
      { seq: 2, method: 'thread/realtime/closed', params: { threadId: 'late-thread', reason: 'stopped' } },
    ]));
    await page.waitForFunction(() => window.__disposeDone === true);
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

for (const failure of ['resume', 'processor']) {
  test(`a ${failure} setup failure stops the native session and acquired media`, async () => {
    const page = await mediaPage(mode => {
      window.__calls = [];
      window.__errors = [];
      window.__track = { stopped: false, stop() { this.stopped = true; } };
      navigator.mediaDevices = { getUserMedia: async () => ({ getTracks: () => [window.__track] }) };
      window.AudioContext = class {
        constructor() { this.sampleRate = 48000; this.destination = {}; window.__context = this; }
        resume() { return mode === 'resume' ? Promise.reject(new Error('resume setup failed')) : Promise.resolve(); }
        createMediaStreamSource() { return { connect() {}, disconnect() { this.disconnected = true; } }; }
        createScriptProcessor() { if (mode === 'processor') throw new Error('processor setup failed'); return { connect() {}, disconnect() {} }; }
        close() { this.closed = true; return Promise.resolve(); }
      };
    }, failure);
    try {
      await page.evaluate(catalog => {
        window.CCCCodexMedia.attach({
          root: document.querySelector('#workspace'), context: { threadId: 'setup-thread', repoPath: '/workspace' }, catalog,
          operation(method, params) {
            window.__calls.push({ method, params });
            if (method === 'thread/realtime/listVoices') return Promise.resolve({ voices: { defaultV1: 'alloy', defaultV2: 'marin', v1: ['alloy'], v2: ['marin'] } });
            return Promise.resolve({});
          }, error(message) { window.__errors.push(message); },
        });
      }, realtimeCatalog());
      await page.click('[data-codex-realtime-start]');
      await page.waitForFunction(() => /setup failed/.test(document.querySelector('[data-codex-realtime-status]').textContent));
      const result = await page.evaluate(() => ({
        stopped: window.__track.stopped,
        closed: window.__context.closed === true,
        stops: window.__calls.filter(call => call.method === 'thread/realtime/stop').length,
        errors: window.__errors,
      }));
      assert.equal(result.stopped, true);
      assert.equal(result.closed, true);
      assert.equal(result.stops, 1);
      assert.equal(result.errors.length, 1);
    } finally { await page.close(); }
  });
}

test('a rejected upload from a stopped session cannot tear down its replacement', async () => {
  const page = await mediaPage(() => {
    window.__calls = [];
    window.__errors = [];
    window.__tracks = [];
    window.__processors = [];
    navigator.mediaDevices = { getUserMedia: async () => {
      const track = { stopped: false, stop() { this.stopped = true; } };
      window.__tracks.push(track);
      return { getTracks: () => [track] };
    } };
    window.AudioContext = class {
      constructor() { this.sampleRate = 48000; this.destination = {}; }
      createMediaStreamSource() { return { connect(node) { window.__processors.push(node); }, disconnect() {} }; }
      createScriptProcessor() { return { onaudioprocess: null, connect() {}, disconnect() {} }; }
      resume() { return Promise.resolve(); }
      close() { return Promise.resolve(); }
    };
  });
  try {
    await page.evaluate(catalog => {
      let rejectFirst;
      window.__rejectFirstUpload = () => rejectFirst(new Error('old upload failed'));
      let uploads = 0;
      window.__controller = window.CCCCodexMedia.attach({
        root: document.querySelector('#workspace'), context: { threadId: 'restart-thread', repoPath: '/workspace' }, catalog,
        operation(method, params) {
          window.__calls.push({ method, params });
          if (method === 'thread/realtime/listVoices') return Promise.resolve({ voices: { defaultV1: 'alloy', defaultV2: 'marin', v1: ['alloy'], v2: ['marin'] } });
          if (method === 'thread/realtime/appendAudio' && uploads++ === 0) return new Promise((_resolve, reject) => { rejectFirst = reject; });
          return Promise.resolve({});
        }, error(message) { window.__errors.push(message); },
      });
    }, realtimeCatalog());
    await page.click('[data-codex-realtime-start]');
    await page.waitForFunction(() => window.__processors.length === 1);
    await page.evaluate(() => window.__controller.handleEvents([
      { seq: 1, method: 'thread/realtime/started', params: { threadId: 'restart-thread', version: 'v2', realtimeSessionId: null } },
    ]));
    await page.evaluate(() => {
      const samples = new Float32Array(480).fill(0.1);
      window.__processors[0].onaudioprocess({ inputBuffer: { numberOfChannels: 1, getChannelData: () => samples } });
    });
    await page.waitForFunction(() => window.__calls.filter(call => call.method === 'thread/realtime/appendAudio').length === 1);
    await page.click('[data-codex-realtime-stop]');
    await page.evaluate(() => window.__controller.handleEvents([
      { seq: 2, method: 'thread/realtime/closed', params: { threadId: 'restart-thread', reason: 'stopped' } },
    ]));
    await page.click('[data-codex-realtime-start]');
    await page.waitForFunction(() => window.__processors.length === 2);
    await page.evaluate(() => {
      window.__controller.handleEvents([{ seq: 3, method: 'thread/realtime/started', params: { threadId: 'restart-thread', version: 'v2', realtimeSessionId: null } }]);
      const samples = new Float32Array(480).fill(0.2);
      window.__processors[1].onaudioprocess({ inputBuffer: { numberOfChannels: 1, getChannelData: () => samples } });
      window.__rejectFirstUpload();
    });
    await page.waitForFunction(() => window.__calls.filter(call => call.method === 'thread/realtime/appendAudio').length >= 2 || window.__tracks[1].stopped);
    const result = await page.evaluate(() => ({
      replacementStopped: window.__tracks[1].stopped,
      uploads: window.__calls.filter(call => call.method === 'thread/realtime/appendAudio').length,
      errors: window.__errors,
    }));
    assert.equal(result.replacementStopped, false);
    assert.equal(result.uploads, 2);
    assert.deepEqual(result.errors, []);
  } finally { await page.close(); }
});

test('output playback stays bounded when the audio clock does not advance', async () => {
  const page = await mediaPage(() => {
    window.__calls = [];
    window.__buffers = 0;
    window.__track = { stop() {} };
    navigator.mediaDevices = { getUserMedia: async () => ({ getTracks: () => [window.__track] }) };
    window.AudioContext = class {
      constructor() { this.sampleRate = 48000; this.currentTime = 5; this.destination = {}; }
      createMediaStreamSource() { return { connect(node) { window.__processor = node; }, disconnect() {} }; }
      createScriptProcessor() { return { onaudioprocess: null, connect() {}, disconnect() {} }; }
      createBuffer() { window.__buffers++; return { copyToChannel() {} }; }
      createBufferSource() { return { connect() {}, start() {}, stop() {} }; }
      resume() { return Promise.resolve(); }
      close() { return Promise.resolve(); }
    };
  });
  try {
    await page.evaluate(catalog => {
      window.__controller = window.CCCCodexMedia.attach({
        root: document.querySelector('#workspace'), context: { threadId: 'playback-thread', repoPath: '/workspace' }, catalog,
        operation(method, params) {
          window.__calls.push({ method, params });
          if (method === 'thread/realtime/listVoices') return Promise.resolve({ voices: { defaultV1: 'alloy', defaultV2: 'marin', v1: ['alloy'], v2: ['marin'] } });
          return Promise.resolve({});
        }, error() {},
      });
    }, realtimeCatalog());
    await page.click('[data-codex-realtime-start]');
    await page.waitForFunction(() => window.__processor);
    await page.evaluate(() => {
      const event = { method: 'thread/realtime/outputAudio/delta', params: {
        threadId: 'playback-thread', audio: { data: btoa('\u0000\u0000'), numChannels: 1, sampleRate: 24000, samplesPerChannel: 1 },
      } };
      window.__controller.handleEvents(Array.from({ length: 80 }, () => event));
    });
    const result = await page.evaluate(() => ({
      buffers: window.__buffers,
      status: document.querySelector('[data-codex-realtime-status]').textContent,
    }));
    assert.ok(result.buffers > 0 && result.buffers < 80, `expected a bounded source count, got ${result.buffers}`);
    assert.match(result.status, /output chunks? dropped/i);
  } finally { await page.close(); }
});

test('a delayed resize never crosses from an exited terminal to its replacement', async () => {
  const page = await mediaPage(() => {
    window.__calls = [];
    window.__execResolvers = [];
    window.ResizeObserver = class {
      constructor(callback) { window.__resizeCallback = callback; }
      observe() {}
      disconnect() {}
    };
  });
  try {
    await page.evaluate(catalog => {
      window.__controller = window.CCCCodexMedia.attach({
        root: document.querySelector('#workspace'), context: { threadId: 'resize-thread', repoPath: '/workspace' }, catalog,
        operation(method, params) {
          window.__calls.push({ method, params });
          if (method === 'command/exec') return new Promise(resolve => window.__execResolvers.push(resolve));
          return Promise.resolve({});
        }, error() {},
      });
      document.querySelector('[data-codex-command]').value = 'first';
      document.querySelector('[data-codex-terminal-form]').requestSubmit();
      window.__resizeCallback([{ contentRect: { width: 960, height: 360 } }]);
    }, terminalCatalog());
    await page.evaluate(async () => {
      window.__execResolvers[0]({ exitCode: 0, stdout: '', stderr: '' });
      await Promise.resolve();
      document.querySelector('[data-codex-command]').value = 'second';
      document.querySelector('[data-codex-terminal-form]').requestSubmit();
    });
    await new Promise(resolve => setTimeout(resolve, 220));
    const result = await page.evaluate(() => ({
      execIds: window.__calls.filter(call => call.method === 'command/exec').map(call => call.params.processId),
      resizeIds: window.__calls.filter(call => call.method === 'command/exec/resize').map(call => call.params.processId),
    }));
    assert.equal(result.execIds.length, 2);
    assert.notEqual(result.execIds[0], result.execIds[1]);
    assert.deepEqual(result.resizeIds, []);
  } finally { await page.close(); }
});

test('realtime capture and controls are gated by each native method', async () => {
  const page = await mediaPage(() => {
    window.__calls = [];
    window.__gumCalls = 0;
    navigator.mediaDevices = { getUserMedia: async () => { window.__gumCalls++; return { getTracks: () => [] }; } };
  });
  try {
    const result = await page.evaluate(async () => {
      const catalog = { methods: ['thread/realtime/start', 'thread/realtime/stop'].map(method => ({ method, available: true })) };
      const controller = window.CCCCodexMedia.attach({
        root: document.querySelector('#workspace'), context: { threadId: 'partial-thread', repoPath: '/workspace' }, catalog,
        operation(method, params) { window.__calls.push({ method, params }); return Promise.resolve({}); }, error() {},
      });
      document.querySelector('[data-codex-realtime-start]').click();
      await Promise.resolve(); await Promise.resolve();
      const first = {
        gumCalls: window.__gumCalls,
        sendDisabled: document.querySelector('[data-codex-realtime-send-text]').disabled,
        speakDisabled: document.querySelector('[data-codex-realtime-speak]')?.disabled === true,
        support: document.querySelector('[data-codex-realtime-support]')?.textContent || '',
      };
      document.querySelector('[data-codex-realtime-stop]').click();
      const secondRoot = document.body.appendChild(document.createElement('div'));
      controller.dispose();
      const noStop = window.CCCCodexMedia.attach({
        root: secondRoot, context: { threadId: 'no-stop-thread', repoPath: '/workspace' },
        catalog: { methods: ['thread/realtime/start', 'thread/realtime/appendAudio'].map(method => ({ method, available: true })) },
        operation(method, params) { window.__calls.push({ method, params }); return Promise.resolve({}); }, error() {},
      });
      const noStopView = {
        startDisabled: secondRoot.querySelector('[data-codex-realtime-start]').disabled,
        support: secondRoot.querySelector('[data-codex-realtime-support]')?.textContent || '',
      };
      noStop.dispose();
      return { first, noStopView, calls: window.__calls };
    });
    assert.equal(result.first.gumCalls, 0);
    assert.equal(result.first.sendDisabled, true);
    assert.equal(result.first.speakDisabled, true);
    assert.match(result.first.support, /microphone input unavailable/i);
    assert.match(result.first.support, /text input unavailable/i);
    assert.match(result.first.support, /speech input unavailable/i);
    assert.ok(result.calls.some(call => call.method === 'thread/realtime/start'));
    assert.ok(result.calls.some(call => call.method === 'thread/realtime/stop'));
    assert.equal(result.noStopView.startDisabled, true);
    assert.match(result.noStopView.support, /stop unavailable/i);
  } finally { await page.close(); }
});

test('an uncertain exec timeout keeps terminal recovery controls active', async () => {
  const page = await mediaPage(() => {
    window.__calls = [];
    window.__errors = [];
    window.ResizeObserver = class { observe() {} disconnect() {} };
  });
  try {
    await page.evaluate(catalog => {
      window.__controller = window.CCCCodexMedia.attach({
        root: document.querySelector('#workspace'), context: { threadId: 'uncertain-thread', repoPath: '/workspace' }, catalog,
        operation(method, params) {
          window.__calls.push({ method, params });
          if (method === 'command/exec') {
            const error = new Error('Native response timed out');
            error.payload = { uncertain: true };
            return Promise.reject(error);
          }
          return Promise.resolve({});
        }, error(message) { window.__errors.push(message); },
      });
      document.querySelector('[data-codex-command]').value = 'long-running';
      document.querySelector('[data-codex-terminal-form]').requestSubmit();
    }, terminalCatalog());
    await page.waitForFunction(() => /could not confirm/i.test(document.querySelector('[data-codex-terminal-status]').textContent));
    await page.evaluate(() => {
      document.querySelector('[data-codex-stdin]').value = 'recover';
      document.querySelector('[data-codex-stdin-send]').click();
      document.querySelector('[data-codex-terminal-stop]').click();
    });
    const result = await page.evaluate(() => ({
      controlsHidden: document.querySelector('[data-codex-terminal-running]').hidden,
      active: window.__controller.isActive(),
      methods: window.__calls.map(call => call.method),
      status: document.querySelector('[data-codex-terminal-status]').textContent,
    }));
    assert.equal(result.controlsHidden, false);
    assert.equal(result.active, true);
    assert.match(result.status, /could not confirm/i);
    assert.ok(result.methods.includes('command/exec/write'));
    assert.ok(result.methods.includes('command/exec/terminate'));
  } finally { await page.close(); }
});

test('dispose releases active local media before dispatching native cleanup', async () => {
  const page = await mediaPage(() => {
    window.__calls = [];
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
        root: document.querySelector('#workspace'), context: { threadId: 'dispose-order-thread', repoPath: '/workspace' }, catalog,
        operation(method, params) {
          window.__calls.push({ method, params });
          if (method === 'thread/realtime/listVoices') return Promise.resolve({ voices: { defaultV1: 'alloy', defaultV2: 'marin', v1: ['alloy'], v2: ['marin'] } });
          if (method === 'thread/realtime/stop') window.__stoppedAtNativeDispatch = window.__track.stopped;
          return Promise.resolve({});
        }, error() {},
      });
    }, realtimeCatalog());
    await page.click('[data-codex-realtime-start]');
    await page.waitForFunction(() => window.__processor);
    await page.evaluate(() => window.__controller.handleEvents([
      { seq: 1, method: 'thread/realtime/started', params: { threadId: 'dispose-order-thread', version: 'v2', realtimeSessionId: null } },
    ]));
    const result = await page.evaluate(async () => {
      window.__disposeDone = false;
      window.__disposePromise = window.__controller.dispose();
      window.__disposePromise.then(() => { window.__disposeDone = true; });
      await Promise.resolve(); await Promise.resolve();
      return {
        stopped: window.__track.stopped,
        closed: window.__context.closed,
        stoppedAtNativeDispatch: window.__stoppedAtNativeDispatch,
        mounted: !!document.querySelector('.codex-media'),
        done: window.__disposeDone,
      };
    });
    assert.deepEqual(result, { stopped: true, closed: true, stoppedAtNativeDispatch: true, mounted: false, done: false });
    await page.evaluate(() => window.__controller.handleEvents([
      { seq: 2, method: 'thread/realtime/closed', params: { threadId: 'dispose-order-thread', reason: 'stopped' } },
    ]));
    await page.waitForFunction(() => window.__disposeDone);
  } finally { await page.close(); }
});

test('dispose waits for authoritative realtime close after releasing local media', async () => {
  const page = await mediaPage(() => {
    window.__calls = [];
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
        root: document.querySelector('#workspace'), context: { threadId: 'dispose-fence-thread', repoPath: '/workspace' }, catalog,
        operation(method, params) {
          window.__calls.push({ method, params });
          if (method === 'thread/realtime/listVoices') return Promise.resolve({ voices: { defaultV1: 'alloy', defaultV2: 'marin', v1: ['alloy'], v2: ['marin'] } });
          return Promise.resolve({});
        }, error() {},
      });
    }, realtimeCatalog());
    await page.click('[data-codex-realtime-start]');
    await page.waitForFunction(() => window.__processor);
    await page.evaluate(() => window.__controller.handleEvents([
      { seq: 10, method: 'thread/realtime/started', params: { threadId: 'dispose-fence-thread', version: 'v2', realtimeSessionId: null } },
    ]));
    const immediate = await page.evaluate(async () => {
      window.__disposeSettled = false;
      window.__disposeError = '';
      window.__disposePromise = window.__controller.dispose();
      window.__disposePromise.then(
        () => { window.__disposeSettled = true; },
        error => { window.__disposeSettled = true; window.__disposeError = error.message; },
      );
      await new Promise(resolve => setTimeout(resolve, 30));
      return {
        stopped: window.__track.stopped,
        contextClosed: window.__context.closed === true,
        mounted: !!document.querySelector('.codex-media'),
        settled: window.__disposeSettled,
      };
    });
    assert.deepEqual(immediate, { stopped: true, contextClosed: true, mounted: false, settled: false });
    await page.evaluate(() => window.__controller.handleEvents([
      { seq: 20, method: 'thread/realtime/closed', params: { threadId: 'dispose-fence-thread', reason: 'stopped' } },
    ]));
    await page.waitForFunction(() => window.__disposeSettled);
    assert.equal(await page.evaluate(() => window.__disposeError), '');
  } finally { await page.close(); }
});

test('disposed controller accepts retiring started then closed events', async () => {
  const page = await mediaPage(() => {
    window.__calls = [];
  });
  try {
    await page.evaluate(catalog => {
      let resolveStart;
      window.__resolveStart = () => resolveStart({});
      window.__controller = window.CCCCodexMedia.attach({
        root: document.querySelector('#workspace'), context: { threadId: 'dispose-starting-thread', repoPath: '/workspace' }, catalog,
        operation(method, params) {
          window.__calls.push({ method, params });
          if (method === 'thread/realtime/listVoices') return Promise.resolve({ voices: { defaultV1: 'alloy', defaultV2: 'marin', v1: ['alloy'], v2: ['marin'] } });
          if (method === 'thread/realtime/start') return new Promise(resolve => { resolveStart = resolve; });
          return Promise.resolve({});
        }, error() {},
      });
    }, realtimeCatalog());
    await page.click('[data-codex-realtime-start]');
    await page.waitForFunction(() => window.__calls.some(call => call.method === 'thread/realtime/start'));
    await page.evaluate(() => {
      window.__disposeSettled = false;
      window.__disposePromise = window.__controller.dispose();
      window.__disposePromise.then(() => { window.__disposeSettled = true; });
      window.__resolveStart();
    });
    await page.waitForFunction(() => window.__calls.some(call => call.method === 'thread/realtime/stop'));
    await new Promise(resolve => setTimeout(resolve, 30));
    assert.equal(await page.evaluate(() => window.__disposeSettled), false);
    await page.evaluate(() => window.__controller.handleEvents([
      { seq: 30, method: 'thread/realtime/started', params: { threadId: 'dispose-starting-thread', version: 'v2', realtimeSessionId: null } },
      { seq: 31, method: 'thread/realtime/closed', params: { threadId: 'dispose-starting-thread', reason: 'stopped' } },
    ]));
    await page.waitForFunction(() => window.__disposeSettled);
  } finally { await page.close(); }
});

test('dispose rejects when authoritative realtime close never arrives', async () => {
  const page = await mediaPage(() => {
    window.__calls = [];
    window.__track = { stopped: false, stop() { this.stopped = true; } };
    navigator.mediaDevices = { getUserMedia: async () => ({ getTracks: () => [window.__track] }) };
    window.AudioContext = class {
      constructor() { this.sampleRate = 48000; this.destination = {}; }
      createMediaStreamSource() { return { connect(node) { window.__processor = node; }, disconnect() {} }; }
      createScriptProcessor() { return { connect() {}, disconnect() {}, onaudioprocess: null }; }
      resume() { return Promise.resolve(); }
      close() { return Promise.resolve(); }
    };
    const realSetTimeout = window.setTimeout.bind(window);
    const realClearTimeout = window.clearTimeout.bind(window);
    window.__closeTimers = [];
    window.setTimeout = (callback, delay, ...args) => {
      if (delay >= 1000) {
        const timer = { callback, cleared: false };
        window.__closeTimers.push(timer);
        return timer;
      }
      return realSetTimeout(callback, delay, ...args);
    };
    window.clearTimeout = timer => {
      if (timer && typeof timer === 'object' && 'cleared' in timer) timer.cleared = true;
      else realClearTimeout(timer);
    };
  });
  try {
    await page.evaluate(catalog => {
      window.__controller = window.CCCCodexMedia.attach({
        root: document.querySelector('#workspace'), context: { threadId: 'dispose-timeout-thread', repoPath: '/workspace' }, catalog,
        operation(method, params) {
          window.__calls.push({ method, params });
          if (method === 'thread/realtime/listVoices') return Promise.resolve({ voices: { defaultV1: 'alloy', defaultV2: 'marin', v1: ['alloy'], v2: ['marin'] } });
          if (method === 'thread/realtime/stop') return new Promise(() => {});
          return Promise.resolve({});
        }, error() {},
      });
    }, realtimeCatalog());
    await page.click('[data-codex-realtime-start]');
    await page.waitForFunction(() => window.__processor);
    await page.evaluate(() => window.__controller.handleEvents([
      { seq: 40, method: 'thread/realtime/started', params: { threadId: 'dispose-timeout-thread', version: 'v2', realtimeSessionId: null } },
    ]));
    const result = await page.evaluate(async () => {
      const pending = window.__controller.dispose();
      await Promise.resolve(); await Promise.resolve();
      const timer = window.__closeTimers.find(entry => !entry.cleared);
      if (timer) timer.callback();
      const outcome = await Promise.race([
        pending.then(() => '', error => error.message),
        new Promise(resolve => setTimeout(() => resolve('still pending'), 50)),
      ]);
      return { error: outcome, stopped: window.__track.stopped, timers: window.__closeTimers.length };
    });
    assert.equal(result.stopped, true);
    assert.equal(result.timers, 1);
    assert.match(result.error, /realtime.*close|close.*realtime/i);
  } finally { await page.close(); }
});

test('native close ordering fences a stopped realtime session from its replacement', async () => {
  const page = await mediaPage(() => {
    window.__calls = [];
    window.__tracks = [];
    window.__processors = [];
    navigator.mediaDevices = { getUserMedia: async () => {
      const track = { stopped: false, stop() { this.stopped = true; } };
      window.__tracks.push(track);
      return { getTracks: () => [track] };
    } };
    window.AudioContext = class {
      constructor() { this.sampleRate = 48000; this.destination = {}; }
      createMediaStreamSource() { return { connect(node) { window.__processors.push(node); }, disconnect() {} }; }
      createScriptProcessor() { return { connect() {}, disconnect() {}, onaudioprocess: null }; }
      resume() { return Promise.resolve(); }
      close() { return Promise.resolve(); }
    };
  });
  try {
    await page.evaluate(catalog => {
      window.__controller = window.CCCCodexMedia.attach({
        root: document.querySelector('#workspace'), context: { threadId: 'close-fence-thread', repoPath: '/workspace' }, catalog,
        operation(method, params) {
          window.__calls.push({ method, params });
          if (method === 'thread/realtime/listVoices') return Promise.resolve({ voices: { defaultV1: 'alloy', defaultV2: 'marin', v1: ['alloy'], v2: ['marin'] } });
          return Promise.resolve({});
        }, error() {},
      });
    }, realtimeCatalog());
    assert.equal(await page.evaluate(() => typeof window.__controller.isActive), 'function');
    assert.equal(await page.evaluate(() => window.__controller.isActive()), false);
    await page.click('[data-codex-realtime-start]');
    await page.waitForFunction(() => window.__processors.length === 1);
    await page.evaluate(() => window.__controller.handleEvents([
      { seq: 10, method: 'thread/realtime/started', params: { threadId: 'close-fence-thread', version: 'v2', realtimeSessionId: null } },
    ]));
    assert.equal(await page.evaluate(() => window.__controller.isActive()), true);
    await page.click('[data-codex-realtime-stop]');
    const stopping = await page.evaluate(() => ({
      startDisabled: document.querySelector('[data-codex-realtime-start]').disabled,
      active: window.__controller.isActive(),
    }));
    assert.deepEqual(stopping, { startDisabled: true, active: true });
    await page.evaluate(() => window.__controller.handleEvents([
      { seq: 15, method: 'thread/realtime/error', params: { threadId: 'close-fence-thread', message: 'old session error' } },
    ]));
    assert.equal(await page.evaluate(() => document.querySelector('[data-codex-realtime-start]').disabled), true);
    assert.equal(await page.evaluate(() => window.__controller.isActive()), true);
    await page.click('[data-codex-realtime-start]');
    assert.equal(await page.evaluate(() => window.__calls.filter(call => call.method === 'thread/realtime/start').length), 1);

    await page.evaluate(() => window.__controller.handleEvents([
      { seq: 20, method: 'thread/realtime/closed', params: { threadId: 'close-fence-thread', reason: 'old session stopped' } },
    ]));
    assert.equal(await page.evaluate(() => window.__controller.isActive()), false);
    await page.click('[data-codex-realtime-start]');
    await page.waitForFunction(() => window.__processors.length === 2);
    await page.evaluate(() => window.__controller.handleEvents([
      { seq: 30, method: 'thread/realtime/started', params: { threadId: 'close-fence-thread', version: 'v2', realtimeSessionId: null } },
    ]));
    const replacement = await page.evaluate(() => ({
      stopped: window.__tracks[1].stopped,
      active: window.__controller.isActive(),
      status: document.querySelector('[data-codex-realtime-status]').textContent,
    }));
    assert.equal(replacement.stopped, false);
    assert.equal(replacement.active, true);
    assert.doesNotMatch(replacement.status, /old session stopped/);
  } finally { await page.close(); }
});

test('audio-output-only realtime creates playback without microphone capture', async () => {
  const page = await mediaPage(() => {
    window.__calls = [];
    window.__gumCalls = 0;
    window.__contexts = 0;
    window.__played = 0;
    navigator.mediaDevices = { getUserMedia: async () => { window.__gumCalls++; return { getTracks: () => [] }; } };
    window.AudioContext = class {
      constructor() { this.sampleRate = 48000; this.currentTime = 1; this.destination = {}; window.__contexts++; }
      createBuffer() { return { copyToChannel() {} }; }
      createBufferSource() { return { connect() {}, start() { window.__played++; }, stop() {} }; }
      resume() { return Promise.resolve(); }
      close() { return Promise.resolve(); }
    };
  });
  try {
    await page.evaluate(() => {
      const catalog = { methods: ['thread/realtime/start', 'thread/realtime/stop'].map(method => ({ method, available: true })) };
      window.__controller = window.CCCCodexMedia.attach({
        root: document.querySelector('#workspace'), context: { threadId: 'output-only-thread', repoPath: '/workspace' }, catalog,
        operation(method, params) { window.__calls.push({ method, params }); return Promise.resolve({}); }, error() {},
      });
      document.querySelector('[data-codex-realtime-start]').click();
    });
    await page.waitForFunction(() => window.__calls.some(call => call.method === 'thread/realtime/start'));
    await page.evaluate(() => window.__controller.handleEvents([
      { seq: 1, method: 'thread/realtime/started', params: { threadId: 'output-only-thread', version: 'v2', realtimeSessionId: null } },
      { seq: 2, method: 'thread/realtime/outputAudio/delta', params: { threadId: 'output-only-thread', audio: { data: btoa('\u0000\u0000'), numChannels: 1, sampleRate: 24000, samplesPerChannel: 1 } } },
    ]));
    const result = await page.evaluate(() => ({ gumCalls: window.__gumCalls, contexts: window.__contexts, played: window.__played }));
    assert.deepEqual(result, { gumCalls: 0, contexts: 1, played: 1 });
  } finally { await page.close(); }
});
