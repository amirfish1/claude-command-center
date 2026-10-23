const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const source = app.slice(app.indexOf('  function ideaRowTitlePreview('), app.indexOf('  function _ideaAvatarHtml('));
const preview = vm.runInNewContext(source + '\nideaRowTitlePreview;');

test('short and exactly forty-character titles stay complete', () => {
  for (const title of ['Short title', 'a'.repeat(40)]) assert.equal(preview(title), title);
  assert.equal(preview('a'.repeat(41)), 'a'.repeat(40) + '…');
});

test('combined letters and joined emoji each count as one visible character', () => {
  for (const character of ['e\u0301', '👩‍💻', '⏳']) {
    const title = character.repeat(40) + ' additional words';
    assert.equal(preview(title), character.repeat(40) + '…');
  }
});
