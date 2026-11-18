const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function harness({embedded = true, enabled = true, master = true, complete = false, dashboard = false, settled = false} = {}) {
  const events = new EventTarget(), timers = [], frames = [];
  let contexts = 0;
  const window = Object.assign(events, {
    AudioContext: class {
      constructor() {
        contexts++;
        this.audioWorklet = {addModule: () => new Promise(() => {})};
      }
    },
    AudioWorkletNode: class {}, isSecureContext: true, __cccArchiveSettled: settled,
  });
  window.parent = embedded ? {} : window;
  const context = vm.createContext({
    window, URLSearchParams, Event, CustomEvent: class extends Event {},
    location: {search: embedded ? '?conv=test-session&embed=app' : ''},
    document: {getElementById: id => dashboard && id === 'convList' ? {} : null, readyState: complete ? 'complete' : 'loading'},
    localStorage: {getItem: key => key === 'ccc-browser-voice' ? (enabled ? '1' : '0')
      : key === 'ccc-tts-enabled' ? (master ? '1' : '0') : null, setItem() {}},
    setTimeout: fn => { timers.push(fn); }, clearTimeout() {},
    requestAnimationFrame: fn => { frames.push(fn); },
  });
  vm.runInContext(fs.readFileSync('static/browser-voice.js', 'utf8'), context);
  return {window, get contexts() { return contexts; },
    emit: name => events.dispatchEvent(new Event(name)),
    frame: () => { const batch = frames.splice(0); batch.forEach(fn => fn()); },
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

test('dashboard automatic voice setup waits for the complete archive and its paint', () => {
  const h = harness({embedded: false, dashboard: true});
  h.emit('load'); h.flush();
  assert.equal(h.contexts, 0);
  h.emit('ccc:archive-settled'); h.flush();
  h.frame(); h.flush();
  assert.equal(h.contexts, 0, 'the complete rows get a paint before audio setup');
  h.frame(); h.flush();
  assert.equal(h.contexts, 1);
});

test('late dashboard script observes settled state and explicit voice startup stays immediate', () => {
  const h = harness({embedded: false, dashboard: true, settled: true, complete: true});
  h.frame(); h.frame(); h.flush();
  assert.equal(h.contexts, 1);
  const explicit = harness({embedded: false, dashboard: true});
  explicit.window.cccBrowserVoice.warm();
  assert.equal(explicit.contexts, 1);
  explicit.emit('ccc:archive-settled'); explicit.frame(); explicit.frame(); explicit.flush();
  assert.equal(explicit.contexts, 1);
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
