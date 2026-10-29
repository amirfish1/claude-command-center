const test = require('node:test');
const assert = require('node:assert/strict');
const QA = require('../static/queue-attention.js');
const now = Date.parse('2026-10-20T12:00:00Z');
const future = {status: 'open', deferred_until: '2026-10-20T12:20:00Z', defer_reason: 'Waiting for scheduled verification'};
test('current deferral beats old delivered answers and exposes exact time and reason', () => {
  const result = QA.project({...future, pending_answer: {state: 'delivered'}}, {now});
  assert.equal(result.key, 'deferred');
  assert.equal(result.label, 'deferred · in 20m');
  assert.match(result.title, /2026-10-20T12:20:00.000Z/);
  assert.match(result.title, /Waiting for scheduled verification/);
  assert.equal(QA.project({...future, pending_answer: {state: 'delivered'}}, {now: now + 1200000}).key, 'answer-delivered');
});
test('legacy blocked state is labeled without inventing an owner question', () => {
  const result = QA.project({status: 'blocked', needs_input: false});
  assert.equal(result.label, 'blocked');
  assert.equal(result.owner, false);
  assert.equal(result.tone, 'neutral');
});
test('future deferrals sort first by due time, not priority or old answer status', () => {
  const rows = [{status:'in_progress'}, {...future, deferred_until:'2026-10-21T12:00:00Z'}, {status:'open'}, future];
  rows.sort((a,b) => QA.compareDeferred(a,b,now));
  assert.equal(rows[0], future);
  assert.equal(rows[1].deferred_until, '2026-10-21T12:00:00Z');
  assert.equal(rows[2].status, 'in_progress');
  assert.equal(QA.compareDeferred({...future,needs_input:true}, {status:'open'}, now), 0);
  assert.equal(QA.compareDeferred({...future,status:'closed'}, {status:'open'}, now), 0);
  assert.equal(QA.compareDeferred(future, {status:'open'}, now + 1200000), 0);
});

test('a duplicate bug names its original rather than implying an owner decision', () => {
  const a = QA.project({status:'blocked', type:'bug', needs_input:false, duplicate_of:'Q-194'});
  assert.equal(a.label, 'waiting on Q-194');
  assert.equal(a.key, 'dependency');
  assert.equal(a.depRef, 'Q-194');
  assert.equal(a.owner, false);
  assert.match(a.title, /no separate worker/);
});
