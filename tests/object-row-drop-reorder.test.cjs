const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const app = fs.readFileSync('static/app.js', 'utf8');

// CCC-1135: Active-mode object groups nest rows inside
// [data-role=current-object-group-rows]; reading group.children found no rows,
// so a row drop bailed before persisting the new parent.
test('reorderObjectSessionRows finds rows nested inside the group', () => {
  const start = app.indexOf('  function reorderObjectSessionRows(');
  const end = app.indexOf('\n  function startInlineObjectRename', start);
  assert.ok(start >= 0 && end > start, 'function found');
  const block = app.slice(start, end);
  assert.ok(!/group\.children/.test(block), 'must not read direct children only');
  assert.match(block, /group\.querySelectorAll\('\.conv-item'\)/);
});
