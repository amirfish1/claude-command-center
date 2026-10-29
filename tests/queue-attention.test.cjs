// CCC-244: who a queue ticket waits on. Red is only for an owner decision;
// everything else comes from structured state, never from comments.
const test = require('node:test');
const assert = require('node:assert/strict');
const QA = require('../static/queue-attention.js');

const BLOCK_AT = '2026-10-20T10:00:00Z';
const blocked = (extra = {}) => ({
  ref: 'Q-1', status: 'in_progress', needs_input: true, blocked_at: BLOCK_AT,
  block_question: 'Which price?',
  history: [{ event: 'claim', at: '2026-10-20T09:00:00Z' }, { event: 'block', at: BLOCK_AT, kind: 'agent' }],
  ...extra,
});

test('a plain block is an owner decision', () => {
  const a = QA.project(blocked());
  assert.equal(a.key, 'owner');
  assert.equal(a.tone, 'owner');
  assert.equal(a.label, 'needs you');
  assert.ok(QA.needsOwner(blocked()));
});

test('a comment after the question is not an answer', () => {
  const it = blocked();
  it.history.push({ event: 'comment', at: '2026-10-20T11:00:00Z', kind: 'human', text: 'approved' });
  const a = QA.project(it);
  assert.equal(a.key, 'owner');
  assert.match(a.title, /1 comment since this question/);
  assert.match(a.title, /not an answer/);
});

test('external wait names the actor and is not owner', () => {
  const it = blocked({ wait: { on: 'external', actor: 'Client A', block_at: BLOCK_AT } });
  const a = QA.project(it);
  assert.equal(a.key, 'external');
  assert.equal(a.tone, 'external');
  assert.equal(a.label, 'waiting on Client A');
  assert.equal(QA.needsOwner(it), false);
});

test('awaiting-client block kind falls back to external without a wait record', () => {
  const a = QA.project(blocked({ block_kind: 'awaiting-client' }));
  assert.equal(a.key, 'external');
  assert.equal(a.label, 'waiting on client');
});

test('a newer block supersedes an older wait record', () => {
  const it = blocked({
    blocked_at: '2026-10-21T10:00:00Z',
    wait: { on: 'external', actor: 'Client A', block_at: BLOCK_AT },
  });
  assert.equal(QA.currentWait(it), null);
  assert.equal(QA.project(it).key, 'owner');
});

test('dependency wait is neutral and names the ref', () => {
  const a = QA.project(blocked({ wait: { on: 'dependency', ref: 'OPS-1', block_at: BLOCK_AT } }));
  assert.equal(a.key, 'dependency');
  assert.equal(a.tone, 'neutral');
  assert.equal(a.label, 'waiting on OPS-1');
});

test('worker wait means the decision is made', () => {
  const a = QA.project(blocked({ wait: { on: 'worker', note: 'Q1 approved', block_at: BLOCK_AT } }));
  assert.equal(a.key, 'worker');
  assert.equal(a.tone, 'answer');
  assert.match(a.title, /Q1 approved/);
});

test('explicit owner wait stays red', () => {
  assert.equal(QA.project(blocked({ wait: { on: 'owner', block_at: BLOCK_AT } })).key, 'owner');
});

test('rationale block is a gate', () => {
  assert.equal(QA.project(blocked({ block_kind: 'rationale' })).key, 'gate');
});

test('closed and in_review project to nothing', () => {
  assert.equal(QA.project(blocked({ status: 'closed' })), null);
  assert.equal(QA.project({ status: 'in_review' }), null);
});

const answered = (state, extra = {}) => ({
  ref: 'Q-2', status: 'open', needs_input: false, blocked_at: BLOCK_AT,
  pending_answer: { state, gen: 1, state_at: '2026-10-20T12:00:00Z' },
  history: [{ event: 'block', at: BLOCK_AT }, { event: 'answer', at: '2026-10-20T12:00:00Z', kind: 'human' }],
  ...extra,
});

test('local answer receipts read queued, sending, delivered, handed off', () => {
  assert.equal(QA.project(answered('queued')).label, 'answer queued');
  assert.equal(QA.project(answered('routing')).label, 'answer queued');
  assert.equal(QA.project(answered('delivering')).label, 'answer sending');
  assert.equal(QA.project(answered('delivered')).label, 'awaiting worker');
  assert.equal(QA.project(answered('handed_off')).label, 'answer handed off');
  assert.equal(QA.project(answered('queued')).tone, 'answer');
  assert.equal(QA.needsOwner(answered('queued')), false);
});

test('a live worker on a delivered answer reads working, a stale one does not', () => {
  const it = answered('delivered', { status: 'in_progress' });
  assert.equal(QA.project(it, { liveClaim: true }).key, 'answer-working');
  assert.equal(QA.project(it, { liveClaim: false }), null);
});

test('github answer event newer than the block reads awaiting worker', () => {
  const it = {
    status: 'open', needs_input: false,
    history: [{ event: 'block', at: BLOCK_AT }, { event: 'answer', at: '2026-10-20T12:00:00Z' }],
  };
  assert.equal(QA.project(it).key, 'answer-pending');
  it.status = 'in_progress';
  it.history.push({ event: 'claim', at: '2026-10-20T13:00:00Z' });
  assert.equal(QA.project(it, { liveClaim: true }).key, 'answer-working');
});

test('an answer older than the latest block does not count', () => {
  const it = {
    status: 'open', needs_input: false,
    history: [{ event: 'answer', at: '2026-10-19T12:00:00Z' }, { event: 'block', at: BLOCK_AT }],
  };
  assert.equal(QA.project(it), null);
});

test('future deferral is neutral, past deferral is nothing', () => {
  const now = Date.parse('2026-10-20T00:00:00Z');
  const a = QA.project({ status: 'open', deferred_until: '2026-10-25T00:00:00Z', defer_reason: 'vendor' }, { now });
  assert.equal(a.key, 'deferred');
  assert.equal(a.tone, 'neutral');
  assert.match(a.title, /vendor/);
  assert.equal(QA.project({ status: 'open', deferred_until: '2026-10-19T00:00:00Z' }, { now }), null);
});
