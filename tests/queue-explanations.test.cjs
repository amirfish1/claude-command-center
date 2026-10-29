const test = require('node:test');
const assert = require('node:assert/strict');
const QA = require('../static/queue-attention.js');
const explain = (item, opts = {}) => QA.project(item, {explain:true, liveClaim:false, ...opts});
test('every unfinished base state has an explanation, without inventing owner input', () => {
  for (const status of ['open','in_progress','in_review','blocked','awaiting_answer','unknown']) {
    const a = explain({status});
    assert.ok(a?.label && a.title, status);
    assert.equal(a.owner, false, status);
  }
  assert.equal(explain({status:'closed'}), null);
});
test('scheduler dependencies override an old delivered answer, but cleared dependencies do not', () => {
  const item = {status:'open', waiting_on:['TASK-1'], pending_answer:{state:'delivered'}};
  assert.equal(explain(item).label, 'waiting on TASK-1');
  assert.equal(explain({...item, waiting_on:[], blocked_by:['TASK-1']}).label, 'awaiting worker');
});
test('unprepared work and queue restrictions explain why workers cannot start', () => {
  assert.equal(explain({status:'open', readiness:'needs-spec'}).label, 'needs specification');
  assert.equal(explain({status:'open'}, {queue:{auto_drain:false}}).key, 'paused');
  assert.equal(explain({status:'open'}, {queue:{desired_workers:0}}).key, 'capacity');
  assert.equal(explain({status:'open', no_auto_drain:true}).key, 'manual');
  assert.equal(explain({status:'open', run_requested:true}, {queue:{auto_drain:false}}).label, 'awaiting worker');
});
test('claims require liveness evidence and unknown eligibility is exposed', () => {
  assert.equal(explain({status:'in_progress'}, {liveClaim:true}).label, 'working');
  const a = explain({status:'in_progress', claimed_machine:'remote', claimed_by:'worker-1'});
  assert.equal(a.label, 'worker not verified');
  assert.match(a.title, /remote/);
  assert.equal(explain({status:'open', claimable:false}).key, 'unexplained');
});
test('amber answer tone only comes from answer/delivery state (CCC-244)', () => {
  assert.notEqual(explain({status:'open'}).tone, 'answer');
  assert.notEqual(explain({status:'in_progress'}, {liveClaim:true}).tone, 'answer');
  assert.equal(explain({status:'in_progress', pending_answer:{state:'queued'}}).tone, 'answer');
  assert.equal(explain({status:'blocked', needs_input:true, wait:{on:'worker'}}).tone, 'answer');
});
test('review and deferred work never count as owner input (CCC-244)', () => {
  const review = explain({status:'in_review'});
  assert.equal(review.key, 'review');
  assert.equal(review.owner, false);
  const now = Date.parse('2026-10-29T00:00:00Z');
  const d = explain({status:'open', deferred_until:'2026-10-29T01:00:00Z'}, {now});
  assert.equal(d.key, 'deferred');
  assert.equal(d.tone, 'neutral');
  assert.equal(d.owner, false);
});
