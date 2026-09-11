const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('static/app.js', 'utf8');

function presentationFor(event) {
  const start = app.indexOf('  // Readable activity log:');
  const end = app.indexOf('  // ── Rail Log pane', start);
  const context = vm.createContext({ Date });
  vm.runInContext(app.slice(start, end), context);
  return context._readableLogPresentation(event);
}

test('idle-TTL kills lead with their CCC originator and reason', () => {
  const presentation = presentationFor({
    verb: 'KILL',
    category: 'kill',
    detail: 'pid=87489 sid=aa98caf9 name=verification source=spawn_idle_ttl idle_hours=1.2 ttl_hours=1.0 last_activity=log@2026-09-07 15:47:11',
  });

  assert.equal(presentation.headline, 'CCC idle-TTL reaper ended a session after 1.2h idle (TTL 1.0h)');
});
