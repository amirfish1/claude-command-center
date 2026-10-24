const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');
const start = source.indexOf('  function sessionRepoAvatarPath(');
const end = source.indexOf('  // end sessionRepoAvatarPath', start);
const context = vm.createContext({repoListState: {repos: [
  {path: '/workspace/project'}, {path: '/workspace/project/nested'}
], suggested: [{path: '/workspace/other'}]}});
vm.runInContext(source.slice(start, end), context);
const resolve = row => context.sessionRepoAvatarPath(row);
test('session directories inherit the nearest repository avatar', () => {
  assert.equal(resolve({session_cwd: '/workspace/project/src/feature'}), '/workspace/project');
  assert.equal(resolve({cwd: '/workspace/project/nested/src'}), '/workspace/project/nested');
  assert.equal(resolve({repo_path: '/workspace/other/src'}), '/workspace/other');
});
test('folder grouping takes precedence over the session working directory', () => {
  assert.equal(resolve({folder_path: '/workspace/project', session_cwd: '/workspace/other'}), '/workspace/project');
});
test('sibling names do not inherit an unrelated avatar', () => {
  assert.equal(resolve({cwd: '/workspace/project-tools'}), '/workspace/project-tools');
  assert.equal(resolve({cwd: '/workspace/project/'}), '/workspace/project');
  assert.equal(resolve({cwd: 'project'}), '');
  assert.equal(resolve(null), '');
});

context.sessionIconPresentation = () => ({engine: 'codex', tier: 'high', working: true, title: 'Codex working'});
context.repoAvatarHtml = root => '<span class="repo-avatar" data-repo-avatar="' + root + '"></span>';
context.escapeAttr = text => text;
context.getEngineSvg = engine => '<svg data-engine="' + engine + '"></svg>';
context._workersHoistState = {engine: '', tier: ''};
const renderStart = source.indexOf('  function sessionEngineIconHtml(');
const renderEnd = source.indexOf('  let _optimisticAgentTimer', renderStart);
vm.runInContext(source.slice(renderStart, renderEnd), context);
test('desktop and header use repo portraits while keeping working and engine metadata', () => {
  for (const location of ['sidebar', 'pane']) {
    const markup = context.sessionEngineIconHtml({cwd: '/workspace/project/src'}, {context: location});
    assert.match(markup, /data-repo-avatar="\/workspace\/project"/);
    assert.match(markup, /has-repo-avatar/);
    assert.match(markup, /codex cost-high is-working/);
    assert.match(markup, /session-activity-dot/);
    assert.match(markup, /title="Codex working"/);
    assert.doesNotMatch(markup, /data-engine=/);
  }
});
test('sessions without a repository retain the engine fallback', () => {
  const markup = context.sessionEngineIconHtml({engine: 'codex'});
  assert.match(markup, /data-engine="codex"/);
  assert.doesNotMatch(markup, /has-repo-avatar/);
});
test('relative folder labels do not mask an absolute repo path', () => {
  assert.equal(resolve({folder_path: 'project', repo_path: '/workspace/project/src'}), '/workspace/project');
  assert.equal(resolve({spawn_cwd: '/workspace/project/src'}), '/workspace/project');
});
test('worker engine hoisting does not move cost text over the repo avatar', () => {
  context._workersHoistState = {engine: 'codex', tier: 'high'};
  const markup = context.sessionEngineIconHtml({cwd: '/workspace/project'});
  assert.match(markup, /has-repo-avatar/);
  assert.doesNotMatch(markup, /hoisted-engine/);
});
