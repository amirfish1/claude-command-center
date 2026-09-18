const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// Pull the TODO-repo helper out of q2.js (same approach as q2-gh-labels).
const q2 = fs.readFileSync(path.join(__dirname, '..', 'static', 'q2.js'), 'utf8');
const start = q2.indexOf('  var TODO_REPO_RE');
const end = q2.indexOf('  // Sort order:', start);
assert.ok(start >= 0 && end > start, 'isTodoRepoQueue helper found');
const state = { configs: {} };
const projectKey = (n) => String(n || '').trim().toUpperCase();
const isTodoRepoQueue = new Function('state', 'projectKey',
  q2.slice(start, end) + '; return isTodoRepoQueue;')(state, projectKey);

test('queues on a repo named TODO (any owner, any case) are pinned', () => {
  state.configs = {
    TODO: { github_repo: 'amirfish1/TODO' },
    PERSONAL: { github_repo: 'amirfish1/TODO' },
    LOWER: { github_repo: 'someone/todo' },
  };
  assert.equal(isTodoRepoQueue({ queue: 'TODO' }), true);
  assert.equal(isTodoRepoQueue({ queue: 'personal' }), true);
  assert.equal(isTodoRepoQueue({ queue: 'LOWER' }), true);
});

test('other repos, local queues and unknown queues are not pinned', () => {
  state.configs = {
    BECKY: { github_repo: 'amirfish1/BYM-Finie' },
    STUFF: { github_repo: 'amirfish1/TODO-archive' },
    LOCAL: {},
  };
  assert.equal(isTodoRepoQueue({ queue: 'BECKY' }), false);
  assert.equal(isTodoRepoQueue({ queue: 'STUFF' }), false);
  assert.equal(isTodoRepoQueue({ queue: 'LOCAL' }), false);
  assert.equal(isTodoRepoQueue({ queue: 'MISSING' }), false);
});

test('renderQueues emits one divider after the pinned group, only when both exist', () => {
  assert.match(q2, /todoCount > 0 && idx === todoCount/);
  assert.match(q2, /q2-qdivider/);
});
