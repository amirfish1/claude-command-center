// Auto-pick decision for the New Session repo guesser (static/app.js
// repoGuessDecide): confident guesses fill the picker unless the user chose,
// middling ones become a suggestion chip, weak ones do nothing.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const start = source.indexOf('  function repoGuessDecide(');
const end = source.indexOf('  // end repoGuessDecide', start);
assert.ok(start > 0 && end > start, 'repoGuessDecide found');
const ctx = vm.createContext({});
vm.runInContext(source.slice(start, end), ctx);
const decide = (g, s) => ctx.repoGuessDecide(g, s);

const A = '/w/alpha';
const B = '/w/beta';

test('confident Jev guess that differs auto-picks', () => {
  assert.deepEqual({ ...decide({ repo_path: B, confidence: 0.97, source: 'jev' }, { current: A, userPicked: false }) },
    { action: 'auto', path: B });
});

test('Jev threshold is inclusive at 0.8', () => {
  assert.equal(decide({ repo_path: B, confidence: 0.8, source: 'jev' }, { current: A, userPicked: false }).action, 'auto');
  assert.equal(decide({ repo_path: B, confidence: 0.79, source: 'jev' }, { current: A, userPicked: false }).action, 'suggest');
});

test('local path/name matches only suggest, at any confidence', () => {
  assert.equal(decide({ repo_path: B, confidence: 0.9, source: 'name' }, { current: A, userPicked: false }).action, 'suggest');
  assert.equal(decide({ repo_path: B, confidence: 1.0, source: 'path' }, { current: A, userPicked: false }).action, 'suggest');
});

test('a manual pick is never overridden, only suggested', () => {
  assert.equal(decide({ repo_path: B, confidence: 1.0, source: 'jev' }, { current: A, userPicked: true }).action, 'suggest');
});

test('below 0.5 shows nothing', () => {
  assert.equal(decide({ repo_path: B, confidence: 0.5, source: 'jev' }, { current: A, userPicked: false }).action, 'suggest');
  assert.equal(decide({ repo_path: B, confidence: 0.49, source: 'jev' }, { current: A, userPicked: false }).action, 'none');
});

test('same folder, no repo, or junk confidence does nothing', () => {
  assert.equal(decide({ repo_path: A, confidence: 0.99 }, { current: A, userPicked: false }).action, 'none');
  assert.equal(decide({ repo_path: null, confidence: 0.99 }, { current: A, userPicked: false }).action, 'none');
  assert.equal(decide({ repo_path: B, confidence: 'x' }, { current: A, userPicked: false }).action, 'none');
  assert.equal(decide(null, { current: A, userPicked: false }).action, 'none');
});
