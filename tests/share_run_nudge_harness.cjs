// Run with: node tests/share_run_nudge_harness.cjs
// Loads static/share-run.js against a stub DOM and checks the nudge rules.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'share-run.js'), 'utf8');
const DAY = 86400000;

function node(tag) {
  return {
    tag, children: [], attrs: {}, classList: { add() {}, remove() {} }, listeners: {},
    set textContent(v) { this.text = v; }, get textContent() { return this.text; },
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(k, fn) { this.listeners[k] = fn; },
    append(...kids) { this.children.push(...kids); },
    replaceChildren(...kids) { this.children = kids; },
    remove() { this.removed = true; },
  };
}

function load({ stored = {}, stats = { ok: true, pending: false, agents: 12, commits: 340 } } = {}) {
  const body = node('body');
  const fetched = [];
  const items = new Map(Object.entries(stored));
  const window = {
    localStorage: {
      getItem: k => (items.has(k) ? items.get(k) : null),
      setItem: (k, v) => items.set(k, v),
    },
  };
  const context = {
    window, body,
    document: {
      body, hidden: false,
      createElement: node,
      addEventListener() {},
      querySelector: () => null,
    },
    fetch: async url => { fetched.push(url); return { json: async () => stats }; },
    setTimeout: () => 0,
    URLSearchParams, encodeURIComponent, Number, String, Object, Promise, Date,
  };
  vm.runInNewContext(source, context);
  return { api: window.cccShareRun, body, fetched, items };
}

(async () => {
  const { api } = load();
  const now = 100 * DAY;
  assert.equal(api.shouldNudge({ now, firstSeen: now, done: false }), false, 'not on day zero');
  assert.equal(api.shouldNudge({ now, firstSeen: now - 2 * DAY, done: false }), true, 'inside week one');
  assert.equal(api.shouldNudge({ now, firstSeen: now - 8 * DAY, done: false }), false, 'never after week one');
  assert.equal(api.shouldNudge({ now, firstSeen: now - 2 * DAY, done: true }), false, 'never twice');

  // First visit only records the date and makes no request at all.
  let run = load();
  await run.api.maybeNudge(now);
  assert.deepEqual(run.fetched, []);
  assert.equal(run.items.get('ccc.shareRun.firstSeen'), String(now));

  // In week one it shows once, using only this dashboard's own API.
  run = load({ stored: { 'ccc.shareRun.firstSeen': String(now - 2 * DAY) } });
  await run.api.maybeNudge(now);
  assert.deepEqual(run.fetched, ['/api/share/stats?days=7']);
  assert.equal(run.body.children.length, 1);
  const text = run.body.children[0].children[0].text;
  assert.match(text, /12 agents, 340 commits/);
  assert.match(text, /stays on this machine/);
  assert.doesNotMatch(text, /star|tweet|post|follow|sign up|invite/i);
  await run.api.maybeNudge(now);
  assert.equal(run.body.children.length, 1, 'second load shows nothing');

  // Nothing to show means no nudge and it may still appear later that week.
  run = load({
    stored: { 'ccc.shareRun.firstSeen': String(now - 2 * DAY) },
    stats: { ok: true, pending: false, agents: 1, commits: 0 },
  });
  await run.api.maybeNudge(now);
  assert.equal(run.body.children.length, 0);
  assert.equal(run.items.has('ccc.shareRun.nudgeDone'), false);

  console.log('share-run nudge harness ok');
})().catch(err => { console.error(err); process.exit(1); });
