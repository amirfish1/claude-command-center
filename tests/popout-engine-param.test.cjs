// The conversation popout rebuilds its one sidebar row from URL params. A
// CCC-spawned Codex session is a registry row with source 'interactive' and
// engine 'codex', and the queued-card Steer decision reads the engine, so
// the popout URL must carry it or the popout never shows Steer.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const slice = (from, to) => {
  const start = app.indexOf(from);
  assert.ok(start >= 0, 'missing ' + from);
  const end = app.indexOf(to, start);
  assert.ok(end > start, 'missing ' + to);
  return app.slice(start, end);
};
const helpers = slice('  function conversationPopoutUrl(', '  function screenPointFromDragEvent(')
  + slice('  function popoutParam(', '  function installSyntheticPopoutRow(')
  + slice('  function queuedSteerSource(', '  // Devin rows keep');

function harness(rows, url) {
  const ctx = {
    URL, URLSearchParams, Number, String, console,
    window: { location: { pathname: '/', href: 'http://127.0.0.1:8091/' } },
    CONV_POPOUT_MODE: true,
    CONV_POPOUT_TARGET: url ? new URL(url).searchParams.get('conv') : '',
    CONV_POPOUT_REPO_PATH: url ? (new URL(url).searchParams.get('repo_path') || '') : '',
    _bootUrlParams: new URLSearchParams(url ? new URL(url).search : ''),
    rowForConversationId: id => rows.find(r => r.id === id || r.session_id === id) || null,
    rowRepoPath: r => (r && r.repo_path) || '',
    popoutRepoPath: () => '',
    paneTitleForRow: r => (r && r.display_name) || '',
    paneCategoryForRow: () => '',
    devinSteerCapableNow: () => false,
  };
  vm.createContext(ctx);
  vm.runInContext(helpers + '\nthis.conversationPopoutUrl = conversationPopoutUrl;'
    + 'this.buildSyntheticPopoutRow = buildSyntheticPopoutRow;'
    + 'this.queuedSteerSource = queuedSteerSource;'
    + 'this.sessionSupportsQueuedSteer = sessionSupportsQueuedSteer;', ctx);
  return ctx;
}

const codexSpawn = { id: 's1', session_id: 's1', source: 'interactive', engine: 'codex', repo_path: '/r', display_name: 'sleeper' };
const claudeRow = { id: 's2', session_id: 's2', source: 'interactive', repo_path: '/r' };

test('popout URL carries the engine beside the source, only when the row has one', () => {
  const h = harness([codexSpawn, claudeRow]);
  const codexUrl = new URL(h.conversationPopoutUrl('s1', '/r'));
  assert.equal(codexUrl.searchParams.get('source'), 'interactive');
  assert.equal(codexUrl.searchParams.get('engine'), 'codex');
  const claudeUrl = new URL(h.conversationPopoutUrl('s2', '/r'));
  assert.equal(claudeUrl.searchParams.get('engine'), null);
});

test('the synthetic popout row reads the engine back and the queued card can steer', () => {
  const opener = harness([codexSpawn]);
  const url = opener.conversationPopoutUrl('s1', '/r');
  const popout = harness([], url);
  const row = popout.buildSyntheticPopoutRow();
  assert.equal(row.source, 'interactive');
  assert.equal(row.engine, 'codex');
  assert.equal(popout.sessionSupportsQueuedSteer(popout.queuedSteerSource(row.source, row.engine)), true);
  // Without the engine the same row reads as plain interactive: no Steer.
  const legacy = harness([], url.replace(/&engine=codex/, ''));
  const legacyRow = legacy.buildSyntheticPopoutRow();
  assert.equal(legacyRow.engine, undefined);
  assert.equal(legacy.sessionSupportsQueuedSteer(legacy.queuedSteerSource(legacyRow.source, legacyRow.engine)), false);
});

test('switching the embedded popout clears the engine with the other per-conversation params', () => {
  assert.match(app, /\['conv', 'conversation', 'session_id', 'source', 'engine', 'title', 'cwd', 'repo_path'\]\.forEach\(k => _bootUrlParams\.delete\(k\)\)/);
});

test('currentSession carries the engine from the row the pane selected', () => {
  const block = slice('    currentSession = {\n      id: sid || null,', '    };');
  assert.match(block, /engine: \(row && row\.engine\) \|\| null,/);
});
