// CCC-244: a GitHub-backed block someone else must clear must not tell the
// owner to answer it.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const QA = require('../static/queue-attention.js');

const source = fs.readFileSync('static/q2.js', 'utf8');
function fn(name) {
  const start = source.search(new RegExp('^  function ' + name + '\\(', 'm'));
  const end = source.indexOf('\n  }', start) + 4;
  assert.ok(start >= 0 && end > start, name + ' exists');
  return source.slice(start, end);
}
const { githubBlockedHint } = new Function('window', 'hasLiveClaim', 'isStaleClaim',
  fn('githubBlockedHint') + fn('attentionOf') + '\nreturn { githubBlockedHint };'
)({ QueueAttention: QA }, () => false, () => false);

const BLOCK_AT = '2026-10-20T10:00:00Z';
const blocked = extra => ({ ref: 'G-1', status: 'open', source: 'github', needs_input: true,
  blocked_at: BLOCK_AT, history: [{ event: 'block', at: BLOCK_AT }], ...extra });

test('owner decision still points the owner to GitHub to answer', () => {
  assert.match(githubBlockedHint(blocked()), /answer it there/);
});

test('external and dependency waits name who must act, not "answer it"', () => {
  const ext = githubBlockedHint(blocked({ wait: { on: 'external', actor: 'Sonia', block_at: BLOCK_AT } }));
  assert.match(ext, /^Waiting on Sonia; nothing for you to answer/);
  assert.doesNotMatch(ext, /needs input|answer it there/);
  const dep = githubBlockedHint(blocked({ wait: { on: 'dependency', ref: 'OPS-1', block_at: BLOCK_AT } }));
  assert.match(dep, /^Waiting on OPS-1; nothing for you to answer/);
});
