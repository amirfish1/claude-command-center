const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Slice the background read pool + startup gate out of app.js and drive it
// with a stub fetch. Boot-time beacons and composer catalogs must wait for
// the archive rows; the archive list, config and mutations must not.
const app = fs.readFileSync('static/app.js', 'utf8');
const start = app.indexOf('  const BACKGROUND_API_READ_LIMIT = 4;');
const end = app.indexOf('  // Pause periodic sidebar/poller work');
assert.ok(start > 0 && end > start, 'could not slice the startup gate out of app.js');
const src = app.slice(start, end);

function harness() {
  const calls = [];
  const baseFetch = (input, init) => {
    calls.push({ url: String(input), method: ((init && init.method) || 'GET').toUpperCase(), signal: init && init.signal });
    return new Promise((resolve, reject) => {
      const sig = init && init.signal;
      const done = () => resolve(new Response('{}', { status: 200 }));
      if (sig) sig.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
      setTimeout(done, 5);
    });
  };
  const ctx = {
    calls,
    window: {
      fetch: baseFetch,
      location: { origin: 'http://127.0.0.1:8090' },
      addEventListener() {},
    },
    localStorage: { getItem() { return null; } },
    Response, AbortController, DOMException, URL, setTimeout, clearTimeout, performance,
    encodeURIComponent, Object, Promise, console,
  };
  vm.createContext(ctx);
  vm.runInContext(src
    + '\nthis.fetchViaGate = (i, o) => window.fetch(i, o);'
    + '\nthis.release = _releaseStartupApiReads;'
    + '\nthis.abortPool = abortBackgroundApiReadsForSpawn;', ctx);
  return ctx;
}

const urls = ctx => ctx.calls.map(c => c.method + ' ' + c.url.replace(/\?.*/, ''));

test('boot beacons and composer catalogs wait for the archive rows', async () => {
  const ctx = harness();
  ctx.fetchViaGate('/api/config');
  ctx.fetchViaGate('/api/spawn-defaults', { cache: 'no-store' });
  ctx.fetchViaGate('/api/model-picker/picks');
  ctx.fetchViaGate('/api/client-log', { method: 'POST', body: '{}' });
  ctx.fetchViaGate('/api/sessions/spawn', { method: 'POST', body: '{}' });
  await new Promise(r => setTimeout(r, 1));
  assert.deepEqual(urls(ctx), [
    'GET /api/conversations/list',
    'GET /api/config',
    'POST /api/sessions/spawn',
  ]);
});

test('released catalog reads survive the spawn-click pool abort', async () => {
  const ctx = harness();
  const spawnDefaults = ctx.fetchViaGate('/api/spawn-defaults', { cache: 'no-store' });
  const models = ctx.fetchViaGate('/api/engines/models');
  const picks = ctx.fetchViaGate('/api/model-picker/picks');
  const folders = ctx.window.__cccBackgroundApiFetch('/api/repo/list');
  const clientLog = ctx.fetchViaGate('/api/client-log', { method: 'POST', body: '{}' });
  ctx.release();
  ctx.abortPool();
  const responses = await Promise.all([spawnDefaults, picks, clientLog, folders, models]);
  assert.deepEqual(responses.map(r => r.status), [200, 200, 200, 200, 200]);
  assert.ok(urls(ctx).includes('GET /api/engines/models'));
  assert.ok(urls(ctx).includes('GET /api/spawn-defaults'));
  assert.ok(urls(ctx).includes('GET /api/model-picker/picks'));
  assert.ok(urls(ctx).includes('GET /api/repo/list'));
  assert.ok(urls(ctx).includes('POST /api/client-log'));
});

test('reader-only popouts release the startup gate at boot', () => {
  // CCC-1209: the gate waits for archive rows a conversation popout never
  // paints, so its usage read (the model pill) sat queued for 20s.
  const at = app.indexOf('  const READER_ONLY_POPOUT = ');
  assert.ok(at > 0);
  const after = app.slice(at, app.indexOf('\n\n', at));
  assert.match(after, /if \(READER_ONLY_POPOUT\) _releaseStartupApiReads\(\);/);
  assert.ok(app.indexOf('function _releaseStartupApiReads') < at, 'gate is defined before the popout release');
});

test('release waits for an in-flight conversation tail (CCC-143)', async () => {
  const ctx = harness();
  let finishOpen;
  const open = new Promise(r => { finishOpen = r; });
  ctx.window.__cccStartupHoldForOpen(open);
  ctx.fetchViaGate('/api/vercel-deploy');
  ctx.release();
  await new Promise(r => setTimeout(r, 20));
  assert.ok(!urls(ctx).includes('GET /api/vercel-deploy'), 'deferred read stays parked while the open is in flight');
  finishOpen({});
  await new Promise(r => setTimeout(r, 20));
  assert.ok(urls(ctx).includes('GET /api/vercel-deploy'), 'settled open releases the queue');
});

test('release without a pending open is immediate', async () => {
  const ctx = harness();
  ctx.fetchViaGate('/api/vercel-deploy');
  ctx.release();
  await new Promise(r => setTimeout(r, 1));
  assert.ok(urls(ctx).includes('GET /api/vercel-deploy'));
});
