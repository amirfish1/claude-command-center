const test = require('node:test');
const assert = require('node:assert/strict');
const bursts = require('../static/wt-log-bursts.js');

// CCC-1182: the reconciler writes one idle evaluation as a 12-line burst
// (IDLE_CANDIDATE + IDLE_SIGNAL×N + IDLE_DECISION) and a GC sweep as one
// GC_RELEASED line per worker. Both log viewers must fold each burst into a
// single summary row; the raw lines stay behind a toggle.

const EVAL = [
  '2026-09-21 15:11:49 UTC  OPS             IDLE_CANDIDATE evaluation_id=idle-4a1a5a940745 queue=OPS worker_id=ops-abc123 session_id=f67fcf91-e571-49cb-aa4d-38fb4d841d3f pid=54493 engine=claude model=x kind=drain released=false floor_s=1800 effective_source=watchtower_stdout effective_age_s=232888',
  '2026-09-21 15:11:49 UTC  OPS             IDLE_SIGNAL evaluation_id=idle-4a1a5a940745 signal=pid_alive value=true source=tracked_pid',
  '2026-09-21 15:11:49 UTC  OPS             IDLE_SIGNAL evaluation_id=idle-4a1a5a940745 signal=pid_identity value=true recorded_start="Fri Sep 18 15:17:59 2026" current_start="Fri Sep 18 15:17:59 2026"',
  '2026-09-21 15:11:49 UTC  OPS             IDLE_SIGNAL evaluation_id=idle-4a1a5a940745 signal=watchtower_stdout path=/logs/ops-abc123.log exists=true mtime=1789770645.7 age_s=232888 error=none',
  '2026-09-21 15:11:49 UTC  OPS             IDLE_SIGNAL evaluation_id=idle-4a1a5a940745 signal=queue_read value=success error=none',
  '2026-09-21 15:11:49 UTC  OPS             IDLE_SIGNAL evaluation_id=idle-4a1a5a940745 signal=owned_by_worker value=0 refs=[]',
  '2026-09-21 15:11:49 UTC  OPS             IDLE_SIGNAL evaluation_id=idle-4a1a5a940745 signal=blocked_refs value=0 refs=[]',
  '2026-09-21 15:11:49 UTC  OPS             IDLE_SIGNAL evaluation_id=idle-4a1a5a940745 signal=pid_signal_planned value=false reason="queue-scoped release never terminates the conversation"',
  '2026-09-21 15:11:49 UTC  OPS             IDLE_DECISION evaluation_id=idle-4a1a5a940745 decision=PRESERVE reasons=["authoritative_activity_unknown","queue_read_error"]',
];

test('an idle evaluation burst collapses to a single item', () => {
  const items = bursts.collapse(EVAL);
  assert.equal(items.length, 1);
  assert.equal(items[0].key, 'idle:idle-4a1a5a940745');
  assert.equal(items[0].members.length, EVAL.length);
});

test('the collapsed idle summary is one line of reconciler thinking', () => {
  const sum = bursts.summary(bursts.collapse(EVAL)[0].members);
  assert.equal(sum.verb, 'IDLE');
  assert.equal(sum.worker, 'ops-abc123');
  assert.match(sum.text, /idle 3d/);           // 232888s ≈ 2.7d → '3d'
  assert.match(sum.text, /floor 30m/);
  assert.match(sum.text, /→ PRESERVE/);
  assert.match(sum.text, /authoritative_activity_unknown, queue_read_error/);
  assert.match(sum.text, /idle-4a1a5a940745/);
});

test('a RELEASE decision surfaces its release_id', () => {
  const lines = EVAL.slice(0, 1)
    .concat(EVAL.slice(1, 4))
    .concat(['2026-09-21 15:11:49 UTC  OPS             IDLE_DECISION evaluation_id=idle-4a1a5a940745 decision=RELEASE reasons=[] release_id=release-24b49f8beacc']);
  const sum = bursts.summary(bursts.collapse(lines)[0].members);
  assert.match(sum.text, /→ RELEASE/);
  assert.match(sum.text, /release-24b49f8beacc/);
  assert.ok(!/reasons/.test(sum.text), 'empty reasons list is omitted');
});

test('adjacent evaluations stay separate bursts', () => {
  const second = EVAL.map(l => l.replace(/idle-4a1a5a940745/g, 'idle-999'));
  const items = bursts.collapse(EVAL.concat(second));
  assert.equal(items.length, 2);
  assert.equal(items[0].key, 'idle:idle-4a1a5a940745');
  assert.equal(items[1].key, 'idle:idle-999');
});

test('non-idle lines interleaved between bursts are untouched', () => {
  const mid = ['2026-09-21 15:11:52 UTC  CCC             CLAIM    CCC-1182 by ccc-58cca65c — text'];
  const items = bursts.collapse(EVAL.concat(mid).concat(EVAL.map(l => l.replace(/idle-4a1a5a940745/g, 'idle-777'))));
  assert.equal(items.length, 3);
  assert.equal(items[1].entry.verb, 'CLAIM');
});

test('a GC sweep burst collapses and summarizes each action', () => {
  const gc = [
    '2026-09-21 15:00:00 UTC  OPS             GC_RELEASED worker ops-a pid 11 sigterm (released 7200s ago, TTL 3600s)',
    '2026-09-21 15:00:00 UTC  OPS             GC_RELEASED worker ops-b pid 22 sigkill (released 9000s ago, TTL 3600s)',
    '2026-09-21 15:00:00 UTC  OPS             GC_RELEASED worker ops-c pid 33 sigterm (released 7200s ago, TTL 3600s)',
  ];
  const items = bursts.collapse(gc);
  assert.equal(items.length, 1);
  const sum = bursts.summary(items[0].members);
  assert.equal(sum.verb, 'GC_RELEASED');
  assert.equal(sum.text, 'ops-a sigterm · ops-b sigkill · ops-c sigterm');
});

test('a lone GC_RELEASED or clipped burst member renders as a plain entry', () => {
  const single = bursts.collapse([
    '2026-09-21 15:00:00 UTC  OPS             GC_RELEASED worker ops-a pid 11 sigterm (released 7200s ago, TTL 3600s)',
  ]);
  assert.equal(single.length, 1);
  assert.equal(single[0].entry.verb, 'GC_RELEASED');
  // A burst clipped to one surviving line (log tail boundary) degrades to a
  // normal row rather than a "1 lines" toggle.
  const clipped = bursts.collapse(EVAL.slice(-1));
  assert.equal(clipped[0].entry.verb, 'IDLE_DECISION');
});

test('unparseable lines pass through as raw items', () => {
  const items = bursts.collapse(['', 'bym-gh-fi', undefined]);
  assert.deepEqual(items.map(i => i.raw), ['', 'bym-gh-fi', 'undefined']);
});

test('kv handles bare, quoted, and list values', () => {
  const d = 'evaluation_id=idle-x decision=PRESERVE reasons=["a","b"] note="two words" refs=[]';
  assert.equal(bursts.kv(d, 'evaluation_id'), 'idle-x');
  assert.equal(bursts.kv(d, 'decision'), 'PRESERVE');
  assert.equal(bursts.kv(d, 'reasons'), '["a","b"]');
  assert.equal(bursts.kv(d, 'note'), 'two words');
  assert.equal(bursts.kv(d, 'refs'), '[]');
  assert.equal(bursts.kv(d, 'missing'), '');
});
