const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('static/app.js', 'utf8');
const start = app.indexOf('  function openQueueTicketComposer(opts) {');
const end = app.indexOf('  // Create and revise the complete durable WatchTower queue configuration.', start);
assert.ok(start >= 0 && end > start, 'queue composer source boundaries exist');

test('queue composer definition does not access modal locals during startup', () => {
  const context = vm.createContext({});
  // Include everything up to the next section: an accidentally detached
  // listener must fail here, not hide outside the extracted function.
  assert.doesNotThrow(() => vm.runInContext(app.slice(start, end), context));
  assert.equal(typeof context.openQueueTicketComposer, 'function');
});
