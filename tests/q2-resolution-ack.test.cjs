const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const source = fs.readFileSync('static/q2.js', 'utf8');
function fn(name) {
  const start = source.search(new RegExp('^  (?:async )?function ' + name + '\\(', 'm'));
  const end = source.indexOf('\n  }', start) + 4;
  assert.ok(start >= 0 && end > start, name + ' exists');
  return source.slice(start, end);
}
const esc = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const helpers = ['resolutionAcked', 'unresolvedNotes', 'resList', 'resolutionHtml',
  'withMachine', 'timelineHtml', 'pickShownClosed', 'operationalBucket'];
const { unresolvedNotes, resolutionHtml, timelineHtml, pickShownClosed, operationalBucket } = new Function(
  'esc', 'window', 'relTime', 'CLOSED_CAP', 'statusOf', 'isLiveWip', 'isWaitingToDrain',
  helpers.map(fn).join('\n') + '\nreturn {' + helpers.join(',') + '};'
)(esc, {}, v => v, 50, it => it.status, () => false, () => false);

const marker = { by: 'reviewer', at: '2026-01-01T00:00:00Z' };
const closed = resolution => ({ ref: 'DEMO-1', status: 'closed', closed_at: '2026-01-01', resolution });

test('partial ack preserves original indexes and keeps only pending notes attention-worthy', () => {
  const item = closed({ unresolved: ['', 'reviewed', 'pending'], unresolved_ack: { 1: marker } });
  const before = JSON.stringify(item);
  assert.deepEqual(unresolvedNotes(item), ['pending']);
  assert.equal(operationalBucket(item), 2);
  assert.equal(JSON.stringify(item), before, 'the original resolution is untouched');
});

test('full ack clears attention, unpins old closes, and undo restores both', () => {
  const item = closed({ unresolved: ['reviewed'], unresolved_ack: { 0: marker } });
  const keep = it => unresolvedNotes(it).length > 0;
  assert.deepEqual(unresolvedNotes(item), []);
  assert.equal(operationalBucket(item), 4);
  assert.deepEqual(pickShownClosed([item], keep, 50), []);
  assert.deepEqual(pickShownClosed([item], keep, 100), [item], 'history can still reveal it');
  delete item.resolution.unresolved_ack['0'];
  assert.deepEqual(unresolvedNotes(item), ['reviewed']);
  assert.equal(operationalBucket(item), 2);
  assert.deepEqual(pickShownClosed([item], keep, 50), [item]);
});

test('legacy resolutions and malformed ack maps do not hide outstanding work', () => {
  for (const resolution of [null, 'legacy summary', {}, { unresolved: 'legacy note' }]) {
    assert.deepEqual(unresolvedNotes(closed(resolution)), []);
  }
  for (const ack of [null, true, '0', [marker], Object.create({ 0: marker })]) {
    assert.deepEqual(unresolvedNotes(closed({ unresolved: ['pending'], unresolved_ack: ack })), ['pending']);
  }
});

test('details retain escaped notes with accessible per-entry ack and undo controls', () => {
  const html = resolutionHtml({ summary: 'done', caveats: ['careful'], follow_ups: ['later'],
    unresolved: ['', '<b>reviewed</b>', 'pending'], unresolved_ack: { 1: marker } }, 'DEMO-1');
  assert.match(html, /is-acked/);
  assert.match(html, /&lt;b&gt;reviewed&lt;\/b&gt;/);
  assert.match(html, /aria-label="Undo acknowledgment unresolved 2"/);
  assert.match(html, /data-q2-ack-index="1" data-q2-ack-undo="1"/);
  assert.match(html, /aria-label="Acknowledge unresolved 3"/);
  assert.match(html, /data-q2-ack-field="caveats"/);
  assert.match(html, /data-q2-ack-field="follow_ups"/);
  assert.doesNotMatch(resolutionHtml({ unresolved: ['historical'] }), /data-q2-ack=/);
});

test('only the latest closed event uses the live resolution and offers controls', () => {
  const item = closed({ unresolved: ['current'], unresolved_ack: { 0: marker } });
  item.timeline = [
    { event: 'close', resolution: { unresolved: ['first close'] } },
    { event: 'reopen', reason: 'retry' },
    { event: 'close', resolution: { unresolved: ['current'] } },
  ];
  const html = timelineHtml(item);
  assert.match(html, /first close/);
  assert.match(html, /current/);
  assert.equal((html.match(/data-q2-ack=/g) || []).length, 1);
  assert.match(html, /↺ Acked/);
  assert.doesNotMatch(timelineHtml({ ...item, status: 'open' }), /data-q2-ack=/);
});

function actionHarness(postJson) {
  const item = closed({ unresolved: ['note'] });
  const state = { items: [item], detail: item, ref: item.ref };
  const messages = [];
  let renders = 0, refreshes = 0;
  const action = new Function('state', 'postJson', 'renderAll', 'note', 'refresh',
    fn('acknowledgeResolution') + '; return acknowledgeResolution;')(
      state, postJson, () => renders++, s => messages.push(s), async () => refreshes++);
  const attrs = { 'data-q2-ack': item.ref, 'data-q2-ack-field': 'unresolved',
    'data-q2-ack-index': '0', 'data-q2-ack-undo': '0' };
  const button = { disabled: false, textContent: 'Acknowledge', getAttribute: k => attrs[k] };
  return { state, action, button, attrs, messages, counts: () => [renders, refreshes] };
}

test('ack uses the existing API, applies server state immediately, and supports undo', async () => {
  const calls = [];
  const h = actionHarness(async (url, payload) => {
    calls.push([url, payload]);
    return { ok: true, item: closed({ unresolved: ['note'],
      unresolved_ack: payload.undo ? {} : { 0: marker } }) };
  });
  await h.action(h.button);
  assert.deepEqual(calls[0], ['/api/ux-fixes/ack', { ref: 'DEMO-1', field: 'unresolved', index: 0, undo: false }]);
  assert.deepEqual(unresolvedNotes(h.state.items[0]), []);
  assert.equal(h.state.detail, h.state.items[0]);
  assert.deepEqual(h.counts(), [1, 1]);
  assert.equal(h.button.disabled, false);
  h.attrs['data-q2-ack-undo'] = '1';
  await h.action(h.button);
  assert.equal(calls[1][1].undo, true);
  assert.deepEqual(unresolvedNotes(h.state.items[0]), ['note']);
});

test('failed ack keeps notes pending and allows retry; navigation does not replace another detail', async () => {
  const h = actionHarness(async () => { throw new Error('offline'); });
  await h.action(h.button);
  assert.equal(h.button.disabled, false);
  assert.deepEqual(unresolvedNotes(h.state.detail), ['note']);
  assert.match(h.messages[0], /offline/);
  assert.deepEqual(h.counts(), [0, 0]);

  const other = closed({ unresolved: ['other ticket'] });
  other.ref = 'DEMO-2';
  const moved = actionHarness(async () => {
    moved.state.ref = other.ref;
    moved.state.detail = other;
    return { item: closed({ unresolved: ['note'], unresolved_ack: { 0: marker } }) };
  });
  await moved.action(moved.button);
  assert.equal(moved.state.detail, other);
  assert.deepEqual(unresolvedNotes(moved.state.items[0]), []);
});
