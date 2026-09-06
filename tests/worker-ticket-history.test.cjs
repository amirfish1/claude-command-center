const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const puppeteer = require('puppeteer');
const app = fs.readFileSync('static/app.js', 'utf8');
const source = app.slice(app.indexOf('  function _uxFixesIdentityKey('), app.indexOf('  function _uxFixesQueueProgressForRow('));
const escapeHtml = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
function harness() {
  const ctx = { uxFixesQueueMeta: null, document: { addEventListener() {} }, escapeHtml, escapeAttr: escapeHtml };
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  return ctx;
}
const row = { session_id: 'session-one', _worker_id: 'worker-one', is_watchtower_worker: true, display_name: '🧵 EXAMPLE#1: Old ticket' };
function ticket(seq, extra = {}) {
  return { seq, project: 'EXAMPLE', ref: `EXAMPLE-${seq}`, title: `Task ${seq}`, status: 'closed', claimed_by: 'worker-one', claimed_session_id: 'session-one', closed_by: 'worker-one', closed_at: new Date(1700000000000 + seq * 1000).toISOString(), ...extra };
}

test('worker history includes every recorded claim and close beyond 25, without duplicates', () => {
  const ctx = harness();
  ctx._setUxFixesQueueMeta([...Array.from({length: 30}, (_, i) => ticket(i + 1)), ticket(31, {status: 'in_progress', closed_by: '', claimed_at: new Date().toISOString()})]);
  const history = ctx._uxFixesWorkerTicketsForRow(row);
  assert.equal(history.length, 31);
  assert.equal(new Set(history.map(t => t.ref)).size, 31);
  assert.equal(history.find(t => t.ref === 'EXAMPLE-31').status, 'in_progress');
});

test('history credits claimants and resolvers separately, never another worker in the same queue', () => {
  const ctx = harness();
  ctx._setUxFixesQueueMeta([
    ticket(1, {closed_by: 'other-worker', closed_session_id: 'session-two'}),
    ticket(2, {status: 'open', closed_by: '', claimed_by: '--claimed-by', claimed_session_id: 'session-one'}),
    ticket(3, {claimed_by: 'other-worker', claimed_session_id: 'session-two', closed_by: 'other-worker'}),
  ]);
  const history = ctx._uxFixesWorkerTicketsForRow(row);
  assert.deepEqual(Array.from(history, t => t.ref).sort(), ['EXAMPLE-1', 'EXAMPLE-2']);
  assert.equal(history.find(t => t.ref === 'EXAMPLE-1').resolved, false);
  assert.equal(history.find(t => t.ref === 'EXAMPLE-1').claimed, true);
  const resolver = ctx._uxFixesWorkerTicketsForRow({session_id: 'session-two'});
  assert.equal(resolver.find(t => t.ref === 'EXAMPLE-1').resolved, true);
  assert.equal(ctx._uxFixesWorkerTicketsForRow({session_id: 'unrelated', display_name: 'EXAMPLE queue worker'}).length, 0);
});

test('historical claim/progress/close actors retain credit after reassignment and reopening', () => {
  const ctx = harness();
  ctx._setUxFixesQueueMeta([ticket(1, {
    status: 'in_progress', claimed_by: 'replacement-worker', claimed_session_id: 'replacement-session',
    closed_by: '', closed_at: '', claimed_at: '2026-09-05T10:00:00Z',
    history: [
      {event: 'claim', at: '2026-09-01T10:00:00Z', by: {kind: 'worker', worker: 'worker-one', session_id: 'session-one'}},
      {event: 'close', at: '2026-09-01T11:00:00Z', by: {kind: 'worker', worker: 'worker-one', session_id: 'session-one'}},
      {event: 'reopen', at: '2026-09-02T10:00:00Z', by: {kind: 'worker', worker: 'unrelated-worker'}},
    ],
    progress_notes: [{at: '2026-09-01T10:30:00Z', by: {kind: 'worker', worker: 'helper-worker', session_id: 'helper-session'}, text: 'Investigated failure'}],
  })]);
  const previous = ctx._uxFixesWorkerTicketsForRow(row);
  assert.equal(previous.length, 1);
  assert.equal(previous[0].resolved, true);
  assert.equal(previous[0].currentClaimed, false);
  assert.equal(previous[0].workedAt, Date.parse('2026-09-01T11:00:00Z'));
  assert.equal(ctx._uxFixesWorkerTicketsForRow({session_id: 'helper-session'}).length, 1);
  assert.equal(ctx._uxFixesWorkerTicketsForRow({_worker_id: 'unrelated-worker'}).length, 0);
  assert.match(ctx._uxFixesWorkerHistoryHtml(row), /Resolved earlier · in progress/);
  // The existing Original ask consumer keeps its closed-ticket contract.
  assert.equal(ctx._uxFixesHandledTicketsForRow(row).length, 0);
});

test('worker names stay stable across tickets and preserve explicit names', () => {
  const ctx = harness();
  assert.equal(typeof ctx._uxFixesWorkerDisplayTitle, 'function');
  const stable = ctx._uxFixesWorkerDisplayTitle(row, row.display_name);
  assert.equal(stable, ctx._uxFixesWorkerDisplayTitle(row, '🧵 EXAMPLE#99: New task'));
  assert.match(stable, /worker/);
  assert.equal(ctx._uxFixesWorkerDisplayTitle({...row, name_overridden: true}, 'My worker'), 'My worker');
  assert.equal(ctx._uxFixesWorkerDisplayTitle(row, 'Custom terminal name'), 'Custom terminal name');
});

test('a row without worker identity drops the ticket prefix its chips already carry', () => {
  const ctx = harness();
  ctx._setUxFixesQueueMeta([ticket(1)]);
  // The Workers lane claims rows on broader evidence than the identity rule,
  // so these reach the tab with "EXAMPLE#1:" printed in the title AND as an
  // EXAMPLE-1 chip below. Keep the prose, drop the duplicated ref.
  const chipped = { session_id: 'session-one', display_name: '🧵 EXAMPLE#1: Old ticket' };
  assert.equal(ctx._uxFixesWorkerDisplayTitle(chipped, chipped.display_name), 'Old ticket');

  // With no chip to carry it, the title is the only place the ref appears.
  const noChips = { session_id: 'session-nine', display_name: '🧵 EXAMPLE#1: Old ticket' };
  assert.equal(ctx._uxFixesWorkerDisplayTitle(noChips, noChips.display_name), '🧵 EXAMPLE#1: Old ticket');

  // A real worker still gets the stable identity name, prose and all.
  assert.match(ctx._uxFixesWorkerDisplayTitle(row, row.display_name), /worker/);
});

test('changes to attributed ticket history invalidate the sidebar content signature', () => {
  const ctx = harness();
  const initial = ctx._setUxFixesQueueMeta([ticket(1), ticket(2)])._sig;
  const updated = ctx._setUxFixesQueueMeta([ticket(1, {title: 'Updated detail'}), ticket(2)])._sig;
  assert.notEqual(updated, initial);
});

test('worker history expands all records and ticket clicks do not select the session row', async () => {
  const browser = await puppeteer.launch({headless: true});
  try {
    const page = await browser.newPage();
    await page.setContent('<div class="conv-item" id="row"></div>');
    await page.evaluate(({source, row, tickets, escapeSource}) => {
      window.escapeHtml = window.escapeAttr = (0, eval)(`(${escapeSource})`);
      window.uxFixesQueueMeta = null;
      (0, eval)(source);
      _setUxFixesQueueMeta(tickets);
      document.getElementById('row').innerHTML = _uxFixesWorkerHistoryHtml(row);
      window.rowClicks = 0;
      document.getElementById('row').addEventListener('click', () => rowClicks++);
      window._uxqOpenItemDetail = ref => { window.openedRef = ref; };
    }, {source, row, tickets: Array.from({length: 30}, (_, i) => ticket(i + 1)), escapeSource: escapeHtml.toString()});
    await page.click('.conv-worker-history summary > span:first-child');
    assert.equal(await page.$eval('.conv-worker-history', el => el.open), true);
    assert.equal(await page.$$eval('.conv-worker-history-list button', els => els.length), 30);
    await page.click('.conv-worker-history-list button');
    assert.equal(await page.evaluate(() => openedRef), 'EXAMPLE-30');
    assert.equal(await page.evaluate(() => rowClicks), 0);
  } finally { await browser.close(); }
});
