// Run with: node tests/share_card_upload_harness.cjs
// Executes the SHARE-CARD block of static/throughput.html against a stub DOM
// and checks what gets uploaded and which text reaches the composer.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../static/throughput.html'), 'utf8');
const src = html.slice(html.indexOf('// SHARE-CARD:BEGIN'), html.indexOf('// SHARE-CARD:END'));

function makeEnv({ uploadOk }) {
  const els = {};
  const handlers = {};
  const ctx = new Proxy({}, {
    get: (t, k) => {
      if (k === 'measureText') return () => ({ width: 10 });
      if (k in t) return t[k];
      if (typeof k === 'string' && k.startsWith('create')) return () => ({ addColorStop() {} });
      return () => {};
    },
    set: (t, k, v) => { t[k] = v; return true; },
  });
  function stub(id) {
    if (els[id]) return els[id];
    const e = {
      id, value: '', textContent: '', checked: false, disabled: false, dataset: {}, width: 0, height: 0, title: '',
      classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
      addEventListener(ev, fn) { handlers[id + ':' + ev] = fn; },
      getContext: () => ctx,
      toBlob(cb) { cb(new Blob([Uint8Array.from([137, 80, 78, 71, e.width & 255, e.height & 255])], { type: 'image/png' })); },
      appendChild() {}, remove() {}, click() {},
    };
    els[id] = e;
    return e;
  }
  const uploads = [];
  const opened = [];
  const clip = { text: null };
  const tiles = ['x', 'linkedin', 'bluesky', 'threads'].map((n) => { const t = stub('tile-' + n); t.dataset.net = n; return t; });
  const sandbox = {
    console, URL, Blob, FormData, AbortController, setTimeout, clearTimeout, crypto: globalThis.crypto,
    Uint8Array, JSON, Math, Date, Number, String, Array, Object, RegExp, Promise, Error, encodeURIComponent,
    weeklyData: { weekly_pct: 40 },
    localStorage: { getItem: () => null, setItem() {} },
    document: {
      getElementById: stub,
      querySelectorAll: (sel) => (sel === '.sh-tile[data-net]' ? tiles : []),
      querySelector: () => stub('qs'),
      addEventListener() {},
      createElement: () => stub('a'),
      body: { appendChild() {} },
    },
    navigator: { clipboard: { write: async () => {}, writeText: async (t) => { clip.text = t; } } },
    ClipboardItem: function () {},
    fetch: async (url, opts) => {
      if (opts && opts.method === 'POST') {
        uploads.push({ url, form: opts.body });
        if (!uploadOk) throw new Error('offline');
        return { ok: true, json: async () => ({ url: 'https://ccc-card.claude-command-center.workers.dev/c/ABCDEFGHIJKLMNOPQRSTUV' }) };
      }
      return { json: async () => ({ daily: [] }) };
    },
  };
  sandbox.window = sandbox;
  sandbox.window.open = (u) => { const tab = { location: { href: '' }, opener: {}, u }; opened.push(tab); return tab; };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  return { sandbox, els, handlers, uploads, opened, clip, tiles };
}

const CARD = 'https://ccc-card.claude-command-center.workers.dev/c/ABCDEFGHIJKLMNOPQRSTUV';
const REPO = 'https://github.com/amirfish1/claude-command-center';
const links = (t) => t.match(/https?:\/\/\S+/g) || [];

(async () => {
  // Upload succeeds: the card page is the only link in the composer text.
  let env = makeEnv({ uploadOk: true });
  env.sandbox.CCCShare.open();
  await env.handlers['tile-x:click']();
  assert.equal(env.uploads.length, 1);
  assert.equal(env.uploads[0].url, 'https://ccc-card.claude-command-center.workers.dev/c');
  const keys = [...env.uploads[0].form.keys()].sort();
  assert.deepEqual(keys, ['image', 'title'], 'only the PNG and headline are uploaded');
  const composer = env.opened[0].location.href;
  assert.match(composer, /^https:\/\/x\.com\/intent\/post\?text=/);
  const text = decodeURIComponent(composer.split('text=')[1]);
  assert.deepEqual(links(text), [CARD]);
  assert.ok(!text.includes(REPO));
  assert.ok(text.includes('getccc.dev'));

  // Identical card is not uploaded twice; other networks reuse the link.
  await env.handlers['tile-bluesky:click']();
  assert.equal(env.uploads.length, 1);
  assert.ok(decodeURIComponent(env.opened[1].location.href).includes(CARD));

  // Copy link puts the card page URL on the clipboard.
  await env.handlers['sh-copylink:click']();
  assert.equal(env.clip.text, CARD);

  // User-edited text keeps their words; the repo link becomes the card link.
  env = makeEnv({ uploadOk: true });
  env.sandbox.CCCShare.open();
  env.els['sh-text'].value = 'my words ' + REPO;
  env.handlers['sh-text:input']();
  await env.handlers['tile-linkedin:click']();
  const edited = decodeURIComponent(env.opened[0].location.href.split('text=')[1]);
  assert.equal(edited, 'my words ' + CARD);

  // Upload fails: falls back to today's text with the repo link.
  env = makeEnv({ uploadOk: false });
  env.sandbox.CCCShare.open();
  await env.handlers['tile-x:click']();
  const fb = decodeURIComponent(env.opened[0].location.href.split('text=')[1]);
  assert.ok(fb.includes(REPO));
  assert.ok(!fb.includes('ccc-card.claude-command-center.workers.dev'));
  assert.match(env.els['sh-note'].textContent, /Could not upload/);
  await env.handlers['sh-copylink:click']();
  assert.match(env.els['sh-note'].textContent, /Could not upload/);

  console.log('ok');
})().catch((e) => { console.error(e); process.exit(1); });
