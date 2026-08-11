(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.Q2WorkerIdle = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function validSeconds(value) {
    return typeof value === 'number' && isFinite(value) && value >= 0;
  }

  function ageText(seconds) {
    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) return minutes + 'm';
    var hours = Math.floor(minutes / 60);
    var remainder = minutes % 60;
    return hours + 'h' + (remainder ? ' ' + remainder + 'm' : '');
  }

  // Mirrors workers.py's RELEASE_IDLE_S: the idle age below which a worker
  // is "warm" (left alone so the queue can reuse it) and above which it
  // becomes release-eligible. Named so q2.js can build a countdown to this
  // boundary instead of hardcoding 30*60 a second time.
  var WARM_CEILING_S = 30 * 60;

  function presentation(seconds) {
    if (!validSeconds(seconds)) {
      return {
        age: '',
        label: 'Idle · age unknown',
        severity: 'unknown',
        title: 'WatchTower did not provide reliable activity evidence.',
      };
    }
    var age = ageText(seconds);
    if (seconds < WARM_CEILING_S) {
      return { age: age, label: 'Idle ' + age + ' · warm for reuse', severity: 'warm', title: 'Idle workers stay warm briefly so the queue can reuse them.' };
    }
    if (seconds < 60 * 60) {
      return { age: age, label: 'Idle ' + age + ' · release pending', severity: 'pending', title: 'WatchTower is waiting to release this idle worker.' };
    }
    if (seconds < 120 * 60) {
      return { age: age, label: 'Idle ' + age + ' · should have released', severity: 'warning', title: 'This worker is past the normal release window.' };
    }
    return { age: age, label: 'Idle ' + age + ' · likely stale', severity: 'stale', title: 'This worker is far past the normal release window and may be stuck.' };
  }

  function signatureBucket(seconds) {
    var state = presentation(seconds);
    return state.severity === 'unknown'
      ? 'unknown'
      : state.severity + ':' + Math.floor(seconds / 60);
  }

  // Mirrors workers.py's RELEASED_TTL_S: the idle age at which a worker
  // holding ONLY blocked (needs-input) tickets stops being preserved
  // indefinitely and is released like any other idle worker (see
  // workers.py _idle_snapshot's blocked_only_past_ceiling). Deliberately
  // its own constant, not derived from the 30/60/120-minute buckets above
  // -- those describe a normal idle worker's release clock, which a
  // blocked-only worker is exempt from entirely until this ceiling hits.
  // The two policies can drift independently if they ever need to.
  var BLOCKED_RELEASE_CEILING_S = 60 * 60;

  return {
    presentation: presentation,
    signatureBucket: signatureBucket,
    ageText: ageText,
    WARM_CEILING_S: WARM_CEILING_S,
    BLOCKED_RELEASE_CEILING_S: BLOCKED_RELEASE_CEILING_S,
  };
});
