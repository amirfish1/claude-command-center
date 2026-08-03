const test = require('node:test');
const assert = require('node:assert/strict');
const idle = require('../static/q2-worker-idle.js');

test('formats approved copy at every lifecycle boundary', () => {
  assert.deepEqual(idle.presentation(null), {
    age: '', label: 'Idle · age unknown', severity: 'unknown',
    title: 'WatchTower did not provide reliable activity evidence.',
  });
  assert.equal(idle.presentation(0).label, 'Idle 0m · warm for reuse');
  assert.equal(idle.presentation(29 * 60 + 59).severity, 'warm');
  assert.equal(idle.presentation(30 * 60).label, 'Idle 30m · release pending');
  assert.equal(idle.presentation(59 * 60 + 59).severity, 'pending');
  assert.equal(idle.presentation(60 * 60).label, 'Idle 1h · should have released');
  assert.equal(idle.presentation(119 * 60 + 59).label, 'Idle 1h 59m · should have released');
  assert.equal(idle.presentation(120 * 60).label, 'Idle 2h · likely stale');
  assert.equal(idle.presentation(128 * 60).label, 'Idle 2h 8m · likely stale');
});

test('invalid ages remain unknown instead of stale', () => {
  for (const value of [-1, NaN, Infinity, true, '7200']) {
    assert.equal(idle.presentation(value).severity, 'unknown');
  }
});

test('signature changes each minute and at severity boundaries', () => {
  assert.equal(idle.signatureBucket(12 * 60), 'warm:12');
  assert.equal(idle.signatureBucket(13 * 60), 'warm:13');
  assert.equal(idle.signatureBucket(30 * 60), 'pending:30');
  assert.equal(idle.signatureBucket(60 * 60), 'warning:60');
  assert.equal(idle.signatureBucket(120 * 60), 'stale:120');
  assert.equal(idle.signatureBucket(null), 'unknown');
});
