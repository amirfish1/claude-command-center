const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function harness({embedded = true, enabled = true, master = true, complete = false} = {}) {
  const events = new EventTarget(), timers = [];
  let contexts = 0;
  const window = Object.assign(events, {
    AudioContext: class {
      constructor() {
        contexts++;
        this.audioWorklet = {addModule: () => new Promise(() => {})};
      }
    },
    AudioWorkletNode: class {}, isSecureContext: true,
  });
  window.parent = embedded ? {} : window;
  const context = vm.createContext({
    window, URLSearchParams, Event, CustomEvent: class extends Event {},
    location: {search: embedded ? '?conv=test-session&embed=app' : ''},
    document: {getElementById: () => null, readyState: complete ? 'complete' : 'loading'},
    localStorage: {getItem: key => key === 'ccc-browser-voice' ? (enabled ? '1' : '0')
      : key === 'ccc-tts-enabled' ? (master ? '1' : '0') : null, setItem() {}},
    setTimeout: fn => { timers.push(fn); }, clearTimeout() {},
  });
  vm.runInContext(fs.readFileSync('static/browser-voice.js', 'utf8'), context);
  return {window, get contexts() { return contexts; },
    emit: name => events.dispatchEvent(new Event(name)),
    flush: () => { while (timers.length) timers.shift()(); }};
}

test('embedded automatic voice setup waits for real content and the following task', () => {
  const h = harness();
  h.emit('load'); h.flush();
  assert.equal(h.contexts, 0, 'document load alone is not a drawn conversation');
  assert.equal(h.window.cccBrowserVoice.enabled, true);
  h.emit('ccc:conversation-drawn');
  assert.equal(h.contexts, 0, 'ready notification can reach the host before audio setup');
  h.flush();
  assert.equal(h.contexts, 1);
  h.emit('ccc:conversation-drawn'); h.flush();
  assert.equal(h.contexts, 1);
});

test('explicit warmup remains immediate while the initial conversation loads', () => {
  const h = harness();
  h.window.cccBrowserVoice.warm();
  assert.equal(h.contexts, 1);
  h.emit('ccc:conversation-drawn'); h.flush();
  assert.equal(h.contexts, 1, 'automatic warmup shares the in-flight initialization');
});

test('normal pages prepare after load, including dynamically loaded scripts', () => {
  for (const complete of [false, true]) {
    const h = harness({embedded: false, complete});
    assert.equal(h.contexts, 0);
    h.emit('load'); h.flush();
    assert.equal(h.contexts, 1);
  }
});

test('voice off and master TTS off do not initialize audio after first paint', () => {
  for (const settings of [{enabled: false}, {master: false}]) {
    const h = harness(settings);
    h.emit('ccc:conversation-drawn'); h.flush();
    assert.equal(h.contexts, 0);
  }
});
