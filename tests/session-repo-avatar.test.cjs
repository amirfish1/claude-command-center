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
context.escapeHtml = text => text;
context.ideaTicketsOn = () => true;
context._hashHue = () => 100;
const portraitStart = source.indexOf('  function _avatarModelBadgeHtml(');
const portraitEnd = source.indexOf('  // Same cleanup as server _archive_list_preview', portraitStart);
vm.runInContext(source.slice(portraitStart, portraitEnd), context);
context.getEngineSvg = engine => '<svg data-engine="' + engine + '"></svg>';
context._workersHoistState = {engine: '', tier: ''};
const renderStart = source.indexOf('  function sessionEngineIconHtml(');
const renderEnd = source.indexOf('  let _optimisticAgentTimer', renderStart);
vm.runInContext(source.slice(renderStart, renderEnd), context);
test('desktop and header use circular repo portraits with the corner model badge', () => {
  for (const location of ['sidebar', 'pane']) {
    const markup = context.sessionEngineIconHtml({cwd: '/workspace/project/src'}, {context: location});
    assert.match(markup, /data-repo-avatar="\/workspace\/project"/);
    assert.match(markup, /has-repo-background/);
    assert.match(markup, /conv-avatar-model codex cost-high/);
    assert.match(markup, /is-working/);
    assert.doesNotMatch(markup, /session-tier-cost/);
    assert.match(markup, /session-activity-dot/);
    assert.match(markup, /title="Codex working"/);
    assert.match(markup, /conv-avatar-model[\s\S]*data-engine="codex"/);
  }
});
test('sessions without a repository retain the engine fallback', () => {
  const markup = context.sessionEngineIconHtml({engine: 'codex'});
  assert.match(markup, /data-engine="codex"/);
  assert.doesNotMatch(markup, /has-repo-background/);
});
test('relative folder labels do not mask an absolute repo path', () => {
  assert.equal(resolve({folder_path: 'project', repo_path: '/workspace/project/src'}), '/workspace/project');
  assert.equal(resolve({spawn_cwd: '/workspace/project/src'}), '/workspace/project');
});
test('ticket numbers and ticket action stay above the repo background', () => {
  context._workersHoistState = {engine: 'codex', tier: 'high'};
  const markup = context.sessionEngineIconHtml({cwd: '/workspace/project', idea_ticket: {ref: 'IDEAS-42'}});
  assert.match(markup, /has-repo-background/);
  assert.match(markup, /conv-avatar-number">42<\/span>/);
  assert.match(markup, /data-role="idea-ticket" data-idea-ref="IDEAS-42"/);
  assert.match(markup, /conv-avatar-model/);
  assert.doesNotMatch(markup, /session-tier-cost/);
});
