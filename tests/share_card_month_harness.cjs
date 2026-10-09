// Run with: node tests/share_card_month_harness.cjs
// Executes the SHARE-CARD block of static/throughput.html against a stub DOM
// and checks the monthly "My October" card:
// month picking, month totals, the card model, post text, deep link.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../static/throughput.html'), 'utf8');
const src = html.slice(html.indexOf('// SHARE-CARD:BEGIN'), html.indexOf('// SHARE-CARD:END'));

const dk = (y, m, d) => y + '-' + String(m).padStart(2, '0') + '-' + String(d).padStart(2, '0');
const row = (day, tokens, engines) => ({
  day, tokens, raw_context_tokens: tokens, cache_read_tokens: Math.round(tokens * 0.9), turns: 10,
  active_duration_sec: 3600, cost_usd: 1.5, engine_tokens: engines || { claude: tokens },
  // Identifying fields must never reach the card even if a payload carried them.
  session_name: 'SECRET-SESSION', folder_path: '/Users/x/SECRET-REPO',
});

// October 2026: active Oct 1-5 and Oct 10-20 (11 day streak); busiest Oct 14.
// One September day and one November day must not leak into October.
const daily = [row(dk(2026, 9, 30), 9e9)];
for (let d = 1; d <= 5; d++) daily.push(row(dk(2026, 10, d), 1e8));
for (let d = 10; d <= 20; d++) daily.push(row(dk(2026, 10, d), d === 14 ? 9e8 : 2e8));
daily.push(row(dk(2026, 11, 1), 7e9));
const PAYLOAD = { ok: true, pending: false, daily };

function makeEnv(search) {
  const els = {};
  const ctx = new Proxy({}, {
    get: (t, k) => {
      if (k === 'measureText') return (s) => ({ width: String(s).length * 10 });
      if (k in t) return t[k];
      if (typeof k === 'string' && k.startsWith('create')) return () => ({ addColorStop() {} });
      return (...a) => { if (k === 'fillText') (t._texts = t._texts || []).push(a[0]); };
    },
    set: (t, k, v) => { t[k] = v; return true; },
  });
  function stub(id) {
    if (els[id]) return els[id];
    const s = new Set();
    const e = {
      id, value: '', textContent: '', checked: false, disabled: false, dataset: {}, title: '', parentNode: { title: '' },
      classList: { add: (c) => s.add(c), remove: (c) => s.delete(c), toggle: (c, on) => ((on === undefined ? !s.has(c) : on) ? s.add(c) : s.delete(c)), contains: (c) => s.has(c) },
      addEventListener() {}, getContext: () => ctx, toBlob(cb) { cb(null); }, appendChild() {}, remove() {}, click() {},
    };
    els[id] = e;
    return e;
  }
  const store = {};
  const sandbox = {
    console, URL, Blob, FormData, AbortController, setTimeout, clearTimeout, crypto: globalThis.crypto,
    JSON, Math, Date, Number, String, Array, Object, RegExp, Promise, Error, encodeURIComponent, URLSearchParams,
    weeklyData: { weekly_pct: 40 },
    location: { search: search || '' },
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } },
    document: {
      getElementById: stub, querySelectorAll: () => [], querySelector: (sel) => stub('qs-' + sel),
      addEventListener() {}, createElement: () => stub('a'), body: { appendChild() {} },
    },
    navigator: { clipboard: { write: async () => {}, writeText: async () => {} } },
    ClipboardItem: function () {},
    fetch: async () => ({ json: async () => PAYLOAD }),
  };
  sandbox.window = sandbox;
  sandbox.window.open = () => null;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  return { sandbox, els, ctx, store };
}

const st = (over) => Object.assign(
  { period: 'cal', metric: 'tokens', size: 'wide', pseudo: false, streak: true, cost: false, saved: true, engines: false, name: '', month: '' },
  over,
);

(async () => {
  const env = makeEnv('');
  const S = env.sandbox.CCCShare;

  // ── calMonth: first week of a month = the month just ended ──
  let cm = S.calMonth(new Date(2026, 10, 1, 9));            // Nov 1
  assert.equal(cm.key, '2026-10');
  assert.equal(cm.complete, true);
  assert.equal(cm.endDay, 31);
  assert.equal(S.calMonth(new Date(2026, 10, 7)).key, '2026-10');
  cm = S.calMonth(new Date(2026, 10, 8));                    // Nov 8: November so far
  assert.equal(cm.key, '2026-11');
  assert.equal(cm.complete, false);
  assert.equal(cm.endDay, 8);
  assert.equal(S.calMonth(new Date(2027, 0, 2)).key, '2026-12');  // year wrap
  assert.equal(S.calMonth(new Date(2026, 10, 1), '2026-09').key, '2026-09');
  assert.equal(S.calMonth(new Date(2026, 10, 1), '2026-13').key, '2026-10');  // bad pick ignored
  assert.equal(S.calMonth(new Date(2026, 10, 1), '2026-12').endDay, 0);       // future month is empty

  // ── summarize over the calendar month only ──
  const nov1 = new Date(2026, 10, 1, 9);
  const sum = S.summarize(PAYLOAD, 'cal', nov1);
  assert.equal(sum.tokens, 5 * 1e8 + 10 * 2e8 + 9e8);
  assert.equal(sum.activeDays, 16);
  assert.equal(sum.longestStreak, 11);
  assert.equal(sum.bestDay, '2026-10-14');
  assert.equal(sum.hours, 16);

  // ── card model ──
  const m = S.cardModel(PAYLOAD, st(), nov1, 40);
  assert.equal(m.kind, 'month');
  assert.equal(m.title, 'My October in Claude Code');
  assert.equal(m.kicker, 'Oct 1-31, 2026');
  assert.equal(m.big, '3.4B');
  assert.equal(m.unit, 'tokens processed');
  assert.deepEqual(JSON.parse(JSON.stringify(m.tiles)), [
    { num: '16', unit: 'agent hours' },
    { num: '16/31', unit: 'active days' },
    { num: '11 days', unit: 'longest streak' },
    { num: 'Oct 14', unit: 'busiest day' },
  ]);
  assert.equal(m.cal.firstDow, 4);            // Oct 1 2026 is a Thursday
  assert.equal(m.cal.levels.length, 31);
  assert.equal(m.cal.best, 14);
  assert.equal(m.cal.levels[13], 4);          // busiest day is the top level
  assert.equal(m.cal.levels[6], 0);           // Oct 7 idle
  assert.ok(!m.extras.some((x) => /day streak/.test(x)), 'no running-streak extra on the month card');
  assert.equal(m.monthKey, '2026-10');

  // Pseudonym goes in the kicker; mixed engines name both products.
  const mixed = { ok: true, daily: [row(dk(2026, 10, 3), 1e9, { claude: 7e8, codex: 3e8 })] };
  const m2 = S.cardModel(mixed, st({ pseudo: true, name: 'demo-otter', metric: 'hours' }), nov1, null);
  assert.equal(m2.title, 'My October in Claude Code + Codex');
  assert.equal(m2.kicker, 'demo-otter · Oct 1-31, 2026');
  assert.deepEqual(JSON.parse(JSON.stringify(m2.tiles[0])), { num: '1B', unit: 'tokens processed' });

  // Month so far
  const m3 = S.cardModel(PAYLOAD, st(), new Date(2026, 9, 20, 15), null);
  assert.equal(m3.kicker, 'Oct 1-20, 2026 (so far)');
  assert.equal(m3.cal.levels[20], -1);        // Oct 21 still to come
  assert.ok(m3.cal.levels[19] >= 1);         // Oct 20 active

  // Plan metric is weekly and falls back on the month card
  assert.equal(S.cardModel(PAYLOAD, st({ metric: 'plan' }), nov1, null).metric, 'tokens');

  // ── post text ──
  const text = S.shareText(m, st());
  assert.equal(text, 'My October in Claude Code: 3.4B tokens, 16 agent hours, 16 active days. Run your own fleet: getccc.dev https://github.com/amirfish1/claude-command-center');
  assert.ok(S.shareText(m3, st()).startsWith('My October so far in Claude Code: '));
  assert.ok(!/—/.test(text));

  // ── drawing: no identifying strings reach the canvas ──
  const canvas = { width: 0, height: 0, getContext: () => env.ctx };
  S.drawCard(canvas, m, 'wide');
  assert.equal(canvas.width, 1200);
  S.drawCard(canvas, m, 'square');
  assert.equal(canvas.height, 1080);
  const drawn = (env.ctx._texts || []).join('\n');
  assert.ok(drawn.includes('My October in Claude Code'));
  assert.ok(drawn.includes('Oct 14'));
  assert.ok(!/SECRET/.test(drawn), 'identifying fields leaked onto the card');

  // ── one click + deep link ──
  S.openMonth('2026-10');
  assert.equal(env.els['share-overlay'].classList.contains('open'), true);
  assert.equal(JSON.parse(env.store['ccc.share.v1']).period, 'cal');
  const env2 = makeEnv('?share=month&m=2026-10');
  assert.equal(env2.els['share-overlay'].classList.contains('open'), true);
  assert.match(env2.els['sh-period-cal'].textContent, /^My October$/);
  // A ?m= pick lasts one page load, it is not restored from storage.
  const env3 = makeEnv('');
  assert.ok(env3.els['month-card-label'].textContent.startsWith('My '));

  console.log('ok');
})().catch((e) => { console.error(e); process.exit(1); });
