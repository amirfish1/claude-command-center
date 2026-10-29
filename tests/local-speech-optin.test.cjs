const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/local-speech.js'), 'utf8');
function setup(fetch, approved = false) {
  const elements = {};
  const timers = new Map();
  let nextTimer = 0;
  class Element {
    constructor() { this.children = []; this.listeners = {}; this.style = {}; }
    set id(value) { this._id = value; elements[value] = this; }
    get id() { return this._id; }
    setAttribute() {}
    addEventListener(name, handler) { this.listeners[name] = handler; }
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.children.push(child); }
    remove() { delete elements[this.id]; }
    click() { return (this.listeners.click || this.onclick)(); }
  }
  const button = new Element(); button.id = 'localSpeechInstall';
  const status = new Element(); status.id = 'localSpeechStatus';
  const store = new Map();
  const window = { cccPopups: { allowed: () => approved }, dispatchEvent() {} };
  const context = { window, fetch, Event: class {},
    document: { getElementById: id => elements[id], createElement: () => new Element(), body: new Element() },
    localStorage: { getItem: key => store.get(key), setItem: (key, value) => store.set(key, value) },
    setTimeout: fn => { const id = ++nextTimer; timers.set(id, fn); return id; },
    clearTimeout: id => timers.delete(id),
  };
  vm.runInNewContext(source, context);
  return { window, button, status, elements, timers };
}
const response = data => ({ ok: true, json: async () => data });
const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

test('default popup gate and status checks never install', async () => {
  let posts = 0;
  const app = setup(async (_, options) => { if (options) posts++; return response({ state: 'not_installed' }); });
  await settle();
  await app.window.cccLocalSpeech.offer();
  assert.equal(posts, 0);
  assert.equal(app.elements.localSpeechOffer, undefined);
});

test('approved offer appears once and only its click installs', async () => {
  let posts = 0;
  const app = setup(async (_, options) => {
    if (options) posts++;
    return response({ state: options ? 'installing' : 'not_installed', progress: 'Downloading offline voice' });
  }, true);
  await settle();
  await app.window.cccLocalSpeech.offer();
  const toast = app.elements.localSpeechOffer;
  assert.ok(toast);
  await app.window.cccLocalSpeech.offer();
  assert.equal(app.elements.localSpeechOffer, toast);
  assert.equal(posts, 0);
  toast.children[0].click();
  await settle();
  assert.equal(posts, 1);
  assert.equal(app.button.disabled, true);
  assert.match(app.status.textContent, /Downloading/);
});

test('click is single-flight, failure gives a retry, ready disables install', async () => {
  let state = 'not_installed', posts = 0;
  const app = setup(async (_, options) => {
    if (options) { posts++; state = 'installing'; }
    return response({ state, reason: state === 'failed' ? 'Download failed. Retry.' : '' });
  });
  await settle();
  const first = app.button.click();
  app.button.click();
  await first;
  assert.equal(posts, 1);
  state = 'failed';
  await Array.from(app.timers.values()).at(-1)();
  await settle();
  assert.equal(app.button.disabled, false);
  assert.match(app.status.textContent, /Download failed/);
  await app.button.click();
  assert.equal(posts, 2);
  state = 'ready';
  await Array.from(app.timers.values()).at(-1)();
  await settle();
  assert.equal(app.button.disabled, true);
  assert.match(app.status.textContent, /Ready/);
});

test('a delayed pre-click status cannot overwrite installing', async () => {
  let release;
  const initial = new Promise(resolve => { release = resolve; });
  const app = setup((_, options) => options ? Promise.resolve(response({ state: 'installing' })) : initial);
  await app.button.click();
  release(response({ state: 'not_installed' }));
  await settle();
  assert.equal(app.button.disabled, true);
  assert.match(app.button.textContent, /Installing/);
  assert.equal(app.timers.size, 1);
});
