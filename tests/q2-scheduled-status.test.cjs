const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const QA = require('../static/queue-attention.js');

const source = fs.readFileSync('static/q2.js', 'utf8');
function fn(name) {
  const start = source.search(new RegExp('^  function ' + name + '\\(', 'm'));
  const end = source.indexOf('\n  }', start) + 4;
  assert.ok(start >= 0 && end > start, name + ' exists');
  return source.slice(start, end);
}
const helpers = ['esc', 'projectKey', 'hasClaimMetadata', 'hasLiveClaim', 'isStaleClaim',
  'isUnverifiedClaim', 'statusOf', 'isLiveWip', 'attentionOf', 'isRunQueued', 'ticketRow'];
const state = { workers: [], queues: [], ref: '', viewAll: false };
const { ticketRow, isStaleClaim, isRunQueued, statusOf } = new Function('window', 'state', `
  const titleOf = it => it.title || it.ref;
  const unresolvedNotes = () => [];
  const relTime = () => '1m ago';
  const statusLabel = st => st;
  const withMachine = by => by;
  const ghLabels = () => [];
  const stagesOf = () => null;
  const isNewTicket = () => false;
  const ICON_TINY_STOP = '', ICON_TINY_PLAY = '';
  const ticketChips = it => '<span>' + (attentionOf(it)?.label || '') + '</span>';
  ${helpers.map(fn).join('\n')}
  return { ticketRow, isStaleClaim, isRunQueued, statusOf };
`)({ QueueAttention: QA }, state);
const item = extra => ({ ref: 'DEMO-1', project: 'DEMO', status: 'open',
  title: 'Scheduled check', ...extra });
const future = () => new Date(Date.now() + 86400000).toISOString();

test('a historical session link is not a stale claim', () => {
  const it = item({ claimed_session_id: 'previous-session' });
  assert.equal(isStaleClaim(it), false);
  assert.equal(statusOf(it), 'open');
  assert.doesNotMatch(ticketRow(it), /stale claim|is-stale-claim/);
  assert.equal(isStaleClaim(item({ status: 'in_progress', claimed_by: 'gone' })), true);
});

test('future deferrals override both old claim metadata and saved run requests', () => {
  const it = item({ deferred_until: future(), claimed_by: 'previous-worker', run_requested: true });
  const html = ticketRow(it);
  assert.equal(isRunQueued(it), false);
  assert.match(html, /deferred · in/);
  assert.doesNotMatch(html, /launching|stale claim|is-stale-claim|is-run-requested/);
});

test('duplicate, dependency and preparation waits cannot appear to be launching', () => {
  for (const extra of [{ duplicate_of: 'DEMO-2' }, { waiting_on: ['DEMO-2'] },
    { needs_input: true }, { readiness: 'needs-spec' }, { status: 'in_review' }]) {
    const it = item({ run_requested: true, ...extra });
    assert.equal(isRunQueued(it), false);
    assert.doesNotMatch(ticketRow(it), /launching|is-run-requested/);
  }
});

test('runnable requests launch after their deferral expires, but live work does not', () => {
  const it = item({ run_requested: true, deferred_until: '2000-01-01T00:00:00Z' });
  assert.equal(isRunQueued(it), true);
  assert.match(ticketRow(it), /launching/);
  state.workers = [{ queue: 'DEMO', worker_id: 'live-worker', session_id: 'live-session' }];
  const live = item({ status: 'in_progress', run_requested: true,
    claimed_by: 'live-worker', claimed_session_id: 'live-session' });
  assert.equal(statusOf(live), 'in_progress');
  assert.equal(isRunQueued(live), false);
  assert.doesNotMatch(ticketRow(live), /launching|stale claim/);
  state.workers = [];
});

test('closed and review records never inherit stale-claim warnings', () => {
  for (const status of ['closed', 'in_review']) {
    const it = item({ status, updated_at: '2026-01-01T00:00:00Z', claimed_by: 'old-worker' });
    assert.equal(isStaleClaim(it), false);
  }
});
