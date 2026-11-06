// The Queue scopes to the open session's repo through _projectForRepoPath. A
// repo whose queue has a different name is configured per install
// (local-overrides.json -> /api/config repo_project_map), never in app.js.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');

function slice(startText, endText) {
  const start = source.indexOf(startText);
  assert.notEqual(start, -1, startText);
  return source.slice(start, source.indexOf(endText, start) + endText.length);
}

function projectFor(repoPath, APP_CONFIG) {
  const code = slice('const _REPO_PROJECT_MAP = {', '};') + '\n'
    + slice('function _projectForRepoPath(repoPath) {', "'');\n  }")
    + '\nresult = _projectForRepoPath(repoPath);';
  const ctx = { repoPath, APP_CONFIG, result: null };
  vm.runInNewContext(code, ctx);
  return ctx.result;
}

test('a configured repo routes to its configured queue', () => {
  const cfg = { repo_project_map: { 'acme+web': 'ACMEPROD' } };
  assert.equal(projectFor('/srv/Apps/Acme+Web', cfg), 'ACMEPROD');
});

test('without config a repo falls back to its normalized folder name', () => {
  assert.equal(projectFor('/srv/Apps/Acme+Web', {}), 'ACMEWEB');
});

test('built-in repos still route with no config', () => {
  assert.equal(projectFor('/srv/Apps/claude-command-center', {}), 'CCC');
});
