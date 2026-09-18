const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const app = fs.readFileSync('static/app.js', 'utf8');
const css = fs.readFileSync('static/app.css', 'utf8');

// CCC-1155: "Attach as sub-session" is drag-and-drop (row onto row), not a
// link-icon button + picker. Detach stays a row button.

test('dropping a session row on a session row writes a manual sub-session edge', () => {
  const start = app.indexOf('  // CCC-1155: dropping one real session row');
  const end = app.indexOf('\n  async function saveConversationOrder(', start);
  assert.ok(start >= 0 && end > start, 'drag attach block found');
  const block = app.slice(start, end);

  assert.match(block, /_attachConvRowAsSubsession\(dstCard\)/, 'drop on a real session row attaches');
  assert.match(block, /setManualSubsessionParent\(childSid, parentSid\)/, 'attach writes the manual edge');
  assert.match(block, /drop-attach/, 'dragover applies the drop-attach affordance');
  assert.match(block, /data-object-drop-zone/, 'object drop-zones keep their reorder semantics');
  assert.match(css, /\.conv-item\.drop-attach/, 'drop-attach styling exists');
});

test('the link-icon attach button and its picker are gone; detach remains', () => {
  assert.ok(!app.includes('data-role="attach-subsession"'), 'attach button removed');
  assert.ok(!app.includes('_openAttachSubsessionPicker'), 'picker removed');
  assert.ok(app.includes('data-role="detach-subsession"'), 'detach button kept');
  assert.ok(app.includes('setManualSubsessionParent(sid, \'\')'), 'detach clears the edge');
});

test('attach refuses cycles and non-session cards', () => {
  const start = app.indexOf('  function _attachConvRowAsSubsession(dstCard) {');
  const end = app.indexOf('\n  function attachDragHandlers', start);
  assert.ok(start >= 0 && end > start, 'attach helper found');
  const block = app.slice(start, end);

  assert.match(block, /_subsessionChainContains\(parentSid, childSid\)/, 'cycle guard consulted');
  assert.match(block, /_isRealSessionCard\(sc\)/, 'backlog/issue cards rejected');
});
