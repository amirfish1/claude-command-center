const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('static/app.js', 'utf8');
const start = app.indexOf("  document.addEventListener('visibilitychange', _resumeForegroundPollers);");
const end = app.indexOf('\n  });', start) + '\n  });'.length;
assert.ok(start >= 0 && end > start);

test('foreground listeners skip initial pageshow and refresh on actual returns', () => {
  const listeners = {};
  let refreshes = 0;
  const target = { addEventListener: (name, callback) => { listeners[name] = callback; } };
  vm.runInNewContext(app.slice(start, end), {
    window: target, document: target,
    _resumeForegroundPollers: () => { refreshes++; },
  });
  listeners.pageshow({ persisted: false });
  assert.equal(refreshes, 0, 'boot owns the first archive fetch and render');
  listeners.pageshow({ persisted: true });
  assert.equal(refreshes, 1, 'browser history restores catch up immediately');
  listeners.focus({ type: 'focus' });
  listeners.visibilitychange({ type: 'visibilitychange' });
  assert.equal(refreshes, 3, 'window and tab returns still refresh');
});
