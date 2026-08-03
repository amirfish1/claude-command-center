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
    if (seconds < 30 * 60) {
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

  return { presentation: presentation, signatureBucket: signatureBucket };
});
