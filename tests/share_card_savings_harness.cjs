// Run with: node tests/share_card_savings_harness.cjs
// Executes the SHARE-CARD block of static/throughput.html against a stub DOM
// and checks the savings metrics: $ saved / $0 runs on the card model, the
// post text, milestone prompting, and graceful degradation with no free runs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../static/throughput.html'), 'utf8');
const src = html.slice(html.indexOf('// SHARE-CARD:BEGIN'), html.indexOf('// SHARE-CARD:END'));

function dayKey(d) {
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}
function addDays(d, n) { return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n); }

const today = new Date();
const PAYLOAD = {
  ok: true,
  daily: [
    { day: dayKey(today), tokens: 50000, raw_context_tokens: 60000, cache_read_tokens: 30000, turns: 40, active_duration_sec: 7200, cost_usd: 8.5, engine_tokens: { claude: 50000 } },
    { day: dayKey(addDays(today, -1)), tokens: 30000, raw_context_tokens: 35000, cache_read_tokens: 15000, turns: 20, active_duration_sec: 3600, cost_usd: 5.0, engine_tokens: { claude: 30000 } },
  ],
  savings: {
    available: true,
    daily: [
      { day: dayKey(today), free_saved_usd: 6.25, free_tokens: 40000, free_runs: 2 },
      { day: dayKey(addDays(today, -3)), free_saved_usd: 120.0, free_tokens: 900000, free_runs: 5 },
    ],
    free_saved_usd: 126.25,
    free_tokens: 940000,
    free_runs: 7,
  },
};

function makeEnv(payload, lsSeed) {
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
  const classSet = (e) => {
    const s = new Set(e._cls || []);
    return {
      add: (c) => { s.add(c); e._cls = [...s]; },
      remove: (c) => { s.delete(c); e._cls = [...s]; },
      toggle: (c, on) => { (on === undefined ? !s.has(c) : on) ? s.add(c) : s.delete(c); e._cls = [...s]; },
      contains: (c) => s.has(c),
    };
  };
  function stub(id) {
    if (els[id]) return els[id];
    const e = {
      id, value: '', textContent: '', checked: false, disabled: false, dataset: {}, width: 0, height: 0, title: '',
      classList: null,
      addEventListener(ev, fn) { handlers[id + ':' + ev] = fn; },
      getContext: () => ctx,
      toBlob(cb) { cb(new Blob([Uint8Array.from([137, 80, 78, 71])], { type: 'image/png' })); },
      appendChild() {}, remove() {}, click() {},
    };
    e.classList = classSet(e);
    els[id] = e;
    return e;
  }
  const store = Object.assign({}, lsSeed);
  const segButtons = {};
  ['period', 'metric', 'size'].forEach((k) => {
    segButtons['#sh-' + k + ' button'] = [];
  });
  const metricBtns = ['tokens', 'hours', 'plan', 'cache', 'saved', 'runs'].map((v) => {
    const b = stub('metric-' + v); b.dataset.v = v; return b;
  });
  const sandbox = {
    console, URL, Blob, FormData, AbortController, setTimeout, clearTimeout, crypto: globalThis.crypto,
    Uint8Array, JSON, Math, Date, Number, String, Array, Object, RegExp, Promise, Error, encodeURIComponent, URLSearchParams,
    weeklyData: { weekly_pct: 40 },
    location: { search: '' },
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
    },
    document: {
      getElementById: stub,
      querySelectorAll: (sel) => {
        if (sel === '#sh-metric button') return metricBtns;
        return [];
      },
      querySelector: (sel) => {
        const m = sel.match(/#sh-metric button\[data-v="(\w+)"\]/);
        if (m) return metricBtns.find((b) => b.dataset.v === m[1]) || null;
        return stub('qs-' + sel);
      },
      addEventListener() {},
      createElement: () => stub('a'),
      body: { appendChild() {} },
    },
    navigator: { clipboard: { write: async () => {}, writeText: async () => {} } },
    ClipboardItem: function () {},
    fetch: async () => ({ json: async () => payload }),
  };
  sandbox.window = sandbox;
  sandbox.window.open = () => ({ location: { href: '' }, opener: {} });
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  return { sandbox, els, handlers, store, metricBtns };
}

const st = (over) => Object.assign(
  { period: 'week', metric: 'saved', size: 'wide', pseudo: false, streak: false, cost: false, saved: true, engines: false, name: '' },
  over,
);

(async () => {
  // ── summarize() folds the savings buckets into the period totals ──
  let env = makeEnv(PAYLOAD);
  const sum = env.sandbox.CCCShare.summarize(PAYLOAD, 'week', today);
  assert.equal(sum.freeSaved, 126.25);
  assert.equal(sum.freeRuns, 7);
  assert.equal(sum.freeTokens, 940000);
  assert.ok(sum.tokens > 0);

  // month/year windows cover the same 365d buckets here
  const sumAll = env.sandbox.CCCShare.summarize(PAYLOAD, 'year', today);
  assert.equal(sumAll.freeSaved, 126.25);

  // ── cardModel: $ saved headline + line + extras ──
  const model = env.sandbox.CCCShare.cardModel(PAYLOAD, st({ metric: 'saved' }), today, 40);
  assert.equal(model.metric, 'saved');
  assert.equal(model.big, '$126');
  assert.equal(model.unit, 'of work for $0');
  assert.equal(JSON.stringify(model.line), JSON.stringify(['7 runs for $0', '80K tokens', '3 agent hours']));

  // $0 runs metric
  const runsModel = env.sandbox.CCCShare.cardModel(PAYLOAD, st({ metric: 'runs' }), today, 40);
  assert.equal(runsModel.big, '7');
  assert.equal(runsModel.unit, 'runs for $0');
  assert.equal(runsModel.line[0], '$126 saved');

  // tokens metric still lists savings as an extra line when enabled
  const tokModel = env.sandbox.CCCShare.cardModel(PAYLOAD, st({ metric: 'tokens' }), today, 40);
  assert.ok(tokModel.extras.some((x) => x.includes('saved $126')), JSON.stringify(tokModel.extras));

  // extras hidden when the checkbox is off
  const noSaved = env.sandbox.CCCShare.cardModel(PAYLOAD, st({ metric: 'tokens', saved: false }), today, 40);
  assert.ok(!noSaved.extras.some((x) => x.includes('saved')));

  // ── mandated post text ──
  const text = env.sandbox.CCCShare.shareText(model, st({ metric: 'saved' }));
  assert.ok(text.startsWith('My AI agents did $126 of work for $0 this week.'), text);
  const runsText = env.sandbox.CCCShare.shareText(runsModel, st({ metric: 'runs' }));
  assert.ok(runsText.startsWith('My AI agents finished 7 runs for $0 this week.'), runsText);

  // ── graceful degradation: no savings block at all ──
  env = makeEnv({ ok: true, daily: PAYLOAD.daily });
  await new Promise((r) => setTimeout(r, 5));
  const bare = env.sandbox.CCCShare.cardModel({ daily: PAYLOAD.daily }, st({ metric: 'saved' }), today, 40);
  assert.equal(bare.metric, 'tokens', 'saved metric falls back to tokens without data');
  const savedBtn = env.metricBtns.find((b) => b.dataset.v === 'saved');
  assert.equal(savedBtn.disabled, true);
  assert.match(savedBtn.title, /free runtime/);

  // ── milestone: $126.25 crosses the $100 threshold once ──
  env = makeEnv(PAYLOAD);
  await new Promise((r) => setTimeout(r, 10));   // let the prefetch land
  assert.equal(env.sandbox.CCCShare.pendingMilestone(), 100);
  const shareBtn = env.els['share-btn'];
  assert.ok(shareBtn.classList.contains('attn'), 'share button pulses on a pending milestone');
  env.sandbox.CCCShare.open();
  await new Promise((r) => setTimeout(r, 10));
  const banner = env.els['sh-milestone'];
  assert.ok(banner.classList.contains('on'), 'milestone banner shows on open');
  assert.match(banner.textContent, /\$100/);
  assert.equal(env.sandbox.CCCShare.pendingMilestone(), 0, 'milestone marked seen after prompt');
  assert.equal(JSON.parse(env.store['ccc.share.v1']).metric, 'saved', 'metric preselects saved');
  assert.ok(!shareBtn.classList.contains('attn'), 'pulse clears once prompted');

  // milestone does not re-fire on the next open
  env.sandbox.CCCShare.close();
  env.sandbox.CCCShare.open();
  await new Promise((r) => setTimeout(r, 5));
  assert.ok(!env.els['sh-milestone'].classList.contains('on'));

  // a stored seen marker suppresses the prompt entirely
  env = makeEnv(PAYLOAD, { 'ccc.share.savingsMilestone': '100' });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(env.sandbox.CCCShare.pendingMilestone(), 0);

  console.log('ok');
})().catch((e) => { console.error(e); process.exit(1); });
