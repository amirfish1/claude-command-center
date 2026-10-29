// $0 spawn runtime — static/free-runtime.js unit coverage.
//
// The composer chip drives runtimeForSpawn (what buildSpawnBody sends) and the
// session badges read isFreeRow/pendingCardRuntimeBit. These functions are
// DOM-free, so a vm context with window/localStorage/document stubs covers
// them — same extraction style as the other *.test.cjs files.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/free-runtime.js'), 'utf8');

function load({ enabled = false } = {}) {
  const listeners = {};
  const storage = { 'ccc.freeRuntime': enabled ? '1' : '0' };
  const ctx = {
    window: {},
    localStorage: {
      getItem: (k) => (k in storage ? storage[k] : null),
      setItem: (k, v) => { storage[k] = String(v); },
    },
    document: {
      readyState: 'loading',
      addEventListener: (ev, fn) => { (listeners[ev] = listeners[ev] || []).push(fn); },
      getElementById: () => null,
      querySelector: () => null,
      createElement: () => ({ style: {}, set textContent(v) {}, appendChild() {} }),
      head: { appendChild() {} },
    },
    fetch: () => Promise.reject(new Error('no router')),
    Promise,
    console,
  };
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  return ctx.window.CCCFreeRuntime;
}

test('runtimeForSpawn stays silent until the chip is on', () => {
  const rt = load({ enabled: false });
  assert.equal(rt.runtimeForSpawn('claude'), '');
  assert.equal(rt.runtimeForSpawn('opencode'), '');
});

test('runtimeForSpawn returns free only for supported engines', () => {
  const rt = load({ enabled: true });
  assert.equal(rt.runtimeForSpawn('claude'), 'free');
  assert.equal(rt.runtimeForSpawn('opencode'), 'free');
  assert.equal(rt.runtimeForSpawn('aider'), 'free');
  // An on-chip for an unsupported engine must not leak runtime=free into
  // a spawn the router cannot serve.
  assert.equal(rt.runtimeForSpawn('codex'), '');
  assert.equal(rt.runtimeForSpawn('gemini'), '');
});

test('isFreeRow matches only the free marker', () => {
  const rt = load();
  assert.equal(rt.isFreeRow({ runtime: 'free' }), true);
  assert.equal(rt.isFreeRow({ runtime: 'FREE' }), true);
  assert.equal(rt.isFreeRow({ runtime: '' }), false);
  assert.equal(rt.isFreeRow({}), false);
  assert.equal(rt.isFreeRow(null), false);
});

test('pendingCardRuntimeBit labels free pending cards', () => {
  const rt = load();
  assert.equal(rt.pendingCardRuntimeBit({ runtime: 'free' }), 'free ($0)');
  assert.equal(rt.pendingCardRuntimeBit({ spawn_body: { runtime: 'free' } }), 'free ($0)');
  assert.equal(rt.pendingCardRuntimeBit({ runtime: '' }), '');
  assert.equal(rt.pendingCardRuntimeBit({}), '');
  assert.equal(rt.pendingCardRuntimeBit(null), '');
});

test('supports lists exactly the router-capable engines', () => {
  const rt = load();
  assert.equal(rt.supports('claude'), true);
  assert.equal(rt.supports('opencode'), true);
  assert.equal(rt.supports('aider'), true);
  assert.equal(rt.supports('codex'), false);
  assert.equal(rt.supports('CLAUDE'), true);
});
