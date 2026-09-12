const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

test('the inline Codex footer does not expose queue ownership controls', () => {
  const client = fs.readFileSync('static/codex-client.js', 'utf8');

  assert.doesNotMatch(client, /data-codex-queue-owner/);
  assert.doesNotMatch(client, /Message queue/);
});
