"""The conversation strip paints without waiting for detail requests."""
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_strip_switch_seeds_row_and_retains_same_session_details():
    source = (ROOT / 'static/app.js').read_text()
    helper = source[source.index('  const _sessionStripCache = new Map();'):source.index('  function scheduleSessionMetadataFetches')]
    program = '''
const assert = require('node:assert/strict');
const _workspaceSessionIdByPane = {}, _workspaceDataByPane = {};
const _usageSessionIdByPane = {}, _usageDataByPane = {};
const _usageRequestGenerationByPane = {}, _usageRequestByPane = {};
const _usageLastFetchByPane = {}, sessionCwdByConv = {};
const renders = [];
const rowRepoPath = row => row.repo_path;
const getInputContextSlot = () => ({querySelector: () => ({innerHTML: 'stale'})});
const renderSessionWorkspaceIntoSticky = pid => renders.push(['workspace', pid]);
const renderSessionUsageIntoStrip = pid => renders.push(['usage', pid]);
const syncInputContextVisibility = () => {};
''' + helper + '''
seedSessionInputContext('a', {id:'a', cwd:'/example/a', model:'model-a', latest_input_tokens:42000}, 'p1');
assert.equal(_workspaceDataByPane.p1.cwd, '/example/a');
assert.equal(_usageDataByPane.p1.latest_input_tokens, 42000);
assert.equal(_usageDataByPane.p1.model, 'model-a');
assert.equal(renders.length, 2);
_workspaceDataByPane.p1 = {cwd:'/example/a', effective_cwd:'/example/worktree', branch:'feature'};
_usageDataByPane.p1 = {model:'live-model', latest_input_tokens:43000};
_usageRequestByPane.p1 = {sid:'a'};
const generation = _usageRequestGenerationByPane.p1;
seedSessionInputContext('b', {id:'b', cwd:'/example/b', model:'model-b', latest_input_tokens:9000}, 'p1');
assert.equal(_workspaceDataByPane.p1.cwd, '/example/b');
assert.equal(_usageDataByPane.p1.model, 'model-b');
assert.equal(_usageDataByPane.p1.latest_input_tokens, 9000);
assert.ok(_usageRequestGenerationByPane.p1 > generation);
assert.equal(_usageRequestByPane.p1, undefined);
seedSessionInputContext('a', {id:'a', cwd:'/example/a'}, 'p1');
assert.equal(_workspaceDataByPane.p1.effective_cwd, '/example/worktree');
assert.equal(_usageDataByPane.p1.model, 'live-model');
assert.equal(_usageDataByPane.p1.latest_input_tokens, 43000);
seedSessionInputContext('a', {id:'a', cwd:'/example/a', model:'new-model', latest_input_tokens:44000}, 'p1');
assert.equal(_workspaceDataByPane.p1.effective_cwd, '/example/worktree');
assert.equal(_usageDataByPane.p1.model, 'new-model');
assert.equal(_usageDataByPane.p1.latest_input_tokens, 44000);
seedSessionInputContext('c', {id:'c', cwd:'/example/c', model:'model-c'}, 'p2');
assert.equal(_usageDataByPane.p1.model, 'new-model');
assert.equal(_usageDataByPane.p2.model, 'model-c');
'''
    subprocess.run(['node', '-e', program], cwd=ROOT, check=True, capture_output=True, text=True)


def test_workspace_enrichment_keeps_seed_visible_and_ignores_other_session():
    source = (ROOT / 'static/app.js').read_text()
    helper = source[source.index('  async function fetchSessionWorkspace'):source.index('  function renderSessionWorkspaceIntoSticky')]
    program = '''
const assert = require('node:assert/strict');
const _workspaceSessionIdByPane = {p1: 'a'};
const seed = {cwd:'/example/a', _initial:true};
const _workspaceDataByPane = {p1:seed};
let complete;
const fetch = () => new Promise(resolve => {complete = resolve;});
const paneIdForSessionId = () => 'p1';
const getInputContextSlot = () => { throw new Error('same-session refresh must not clear DOM'); };
let rendered = 0;
const renderSessionWorkspaceIntoSticky = () => {rendered++;};
const convPaneElById = () => null;
''' + helper + '''
(async () => {
  const pending = fetchSessionWorkspace('a', 'p1');
  assert.equal(_workspaceDataByPane.p1, seed);
  complete({json:async () => ({cwd:'/example/a', branch:'main'})});
  await pending;
  assert.equal(_workspaceDataByPane.p1.branch, 'main');
  assert.equal(rendered, 1);
  const stale = fetchSessionWorkspace('a', 'p1');
  _workspaceSessionIdByPane.p1 = 'b';
  _workspaceDataByPane.p1 = {cwd:'/example/b'};
  complete({json:async () => ({cwd:'/example/a', branch:'stale'})});
  await stale;
  assert.equal(_workspaceDataByPane.p1.cwd, '/example/b');
  assert.equal(rendered, 1);
})().catch(err => {console.error(err); process.exitCode = 1;});
'''
    subprocess.run(['node', '-e', program], cwd=ROOT, check=True, capture_output=True, text=True)
