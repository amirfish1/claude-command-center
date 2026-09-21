const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const app = fs.readFileSync('static/app.js', 'utf8');

// CCC-1169: a backgrounded tab throttles the timer-driven archive pipeline
// while performance.now() keeps counting, so placeholder->rows gaps measured
// across a hidden/frozen stretch recorded as multi-minute "loads" (368s/223s
// samples with status 200 and unsaturated load). Both perf metrics now
// subtract time spent hidden/frozen and ship the raw wall time as
// detail.wall_ms.
//
// CCC-1181: an occluded window / suspended renderer stops JS for minutes
// with document.hidden still false — no visibilitychange/freeze fires — so
// event-driven tracking misses it (143s "cold" archive_load). A heartbeat
// beat now flags much-larger-than-scheduled gaps as inactive segments.

function loadPerf() {
  const start = app.indexOf('  const _perfHiddenSegs = [];');
  assert.ok(start >= 0, 'hidden-time accounting found');
  const end = app.indexOf('\n  // Single source of truth', start);
  assert.ok(end > start, 'end of perf block found');
  const body = app.slice(start, end);
  let now = 0;
  const handlers = {};
  const document = {
    hidden: false,
    addEventListener: (ev, fn) => { handlers[ev] = fn; },
  };
  const performance = { now: () => now };
  // Shadow setInterval so the heartbeat registers without keeping a real
  // 2s timer alive past the test run; _perfBeatCheck is driven manually.
  const setInterval = () => 0;
  const out = new Function('document', 'performance', 'setInterval',
    body + '; return { active: _perfActiveElapsed, beat: _perfBeatCheck };')(
    document, performance, setInterval);
  return {
    active: out.active,
    beat: out.beat,
    setNow: (v) => { now = v; },
    hide: () => handlers.visibilitychange && (document.hidden = true, handlers.visibilitychange()),
    show: () => handlers.visibilitychange && (document.hidden = false, handlers.visibilitychange()),
    freeze: () => handlers.freeze && handlers.freeze(),
    resume: () => handlers.resume && handlers.resume(),
  };
}

test('no hidden time: active elapsed equals wall elapsed', () => {
  const p = loadPerf();
  p.setNow(1000);
  assert.equal(p.active(0, 1000), 1000);
});

test('a mostly-hidden window measures only its visible edges', () => {
  const p = loadPerf();
  p.setNow(10); p.hide();          // tab hidden just after t0
  p.setNow(369000); p.show();      // back 6 minutes later
  // window [0, 370000] -> 10ms before hiding + 1000ms after resume
  assert.equal(p.active(0, 370000), 1010);
});

test('partial overlap subtracts only the hidden slice', () => {
  const p = loadPerf();
  p.setNow(50); p.hide();
  p.setNow(80); p.show();
  assert.equal(p.active(0, 200), 170);
});

test('still hidden at fire time counts through the end of the window', () => {
  const p = loadPerf();
  p.setNow(150); p.hide();
  assert.equal(p.active(0, 200), 150);
});

test('hidden segments outside the window are ignored', () => {
  const p = loadPerf();
  p.setNow(10); p.hide();
  p.setNow(20); p.show();          // hidden [10,20], before the window
  assert.equal(p.active(100, 300), 200);
});

test('freeze/resume cover the no-visibility-transition path', () => {
  const p = loadPerf();
  p.setNow(100); p.freeze();
  p.setNow(400); p.resume();
  assert.equal(p.active(0, 500), 200);
});

test('a suspension gap with no lifecycle event is subtracted', () => {
  const p = loadPerf();
  p.setNow(2000); p.beat();            // healthy tick records the baseline
  p.setNow(160000); p.beat();          // page suspended ~158s, no events fired
  // window [0,165000] -> 2s before the freeze + 5s after the wake
  assert.equal(p.active(0, 165000), 7000);
});

test('a sub-threshold heartbeat gap still counts as active', () => {
  const p = loadPerf();
  p.setNow(10000); p.beat();           // 10s gap < 15s flag threshold
  p.setNow(12000); p.beat();
  assert.equal(p.active(0, 12000), 12000);
});

test('a suspension gap inside a hidden span is not double-counted', () => {
  const p = loadPerf();
  p.setNow(100); p.hide();
  p.setNow(150000); p.beat();          // beat resumes while still hidden
  p.setNow(151000); p.show();
  // hidden [100,151000] -> 100ms before + 1000ms after = 1100
  assert.equal(p.active(0, 152000), 1100);
});

test('a suspension segment overlapping only part of the window', () => {
  const p = loadPerf();
  p.setNow(5000); p.beat();
  p.setNow(200000); p.beat();          // gap segment [5000,200000]
  // window [100000,210000] -> only the post-wake 10s is active
  assert.equal(p.active(100000, 210000), 10000);
});

test('a heartbeat gap overlapping a closed hidden span merges, not double-counts', () => {
  const p = loadPerf();
  p.setNow(100); p.hide();
  p.setNow(150000); p.show();          // wake: hidden span [100,150000] closes first
  p.beat();                            // then the beat flags gap [0,150000]
  // union is [0,150000] -> 2s of visible time after the wake, not <=0
  assert.equal(p.active(0, 152000), 2000);
});

test('both metrics report active ms plus wall_ms/hidden_ms detail', () => {
  const arc = app.indexOf("_perfEvent('archive_load'");
  const conv = app.indexOf("_perfEvent('conv_open'");
  assert.ok(arc > 0 && conv > 0);
  for (const at of [arc, conv]) {
    const body = app.slice(at - 400, at + 700);
    assert.match(body, /_perfActiveElapsed\(_perf\w+\.??t0|_perfActiveElapsed\(_perfArchiveT0|_perfActiveElapsed\(_perfConvOpen\.t0/);
    assert.match(body, /wall_ms: Math\.round\(_perfWall\)/);
    assert.match(body, /hidden_ms: Math\.round\(_perfWall - _perfActive\)/);
  }
});
