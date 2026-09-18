const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const app = fs.readFileSync('static/app.js', 'utf8');

// CCC-1150: the composer speaker button must read the focused pane in split
// view, not always pane p1.
test('composer speaker button reads the active pane, not a hardcoded p1', () => {
  const m = app.match(/\$convTtsBtn\.addEventListener\('click'[^;]*;/);
  assert.ok(m, 'convTtsBtn click handler exists');
  const handler = m[0];
  assert.ok(
    !handler.includes("readLastMessageAloud('p1')"),
    'click handler must not hardcode pane p1'
  );
  assert.ok(
    handler.includes('readLastMessageAloud(_ttsActivePaneId || activePaneId())'),
    'click handler should use the same active-pane expression as the other TTS controls'
  );
});
