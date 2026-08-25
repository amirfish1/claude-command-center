const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('static/app.js', 'utf8');
const html = fs.readFileSync('static/index.html', 'utf8');

function extractFunction(name) {
  const start = app.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `function ${name} not found in app.js`);
  let cursor = app.indexOf('{', start);
  let depth = 0;
  for (; cursor < app.length; cursor++) {
    if (app[cursor] === '{') depth += 1;
    else if (app[cursor] === '}') {
      depth -= 1;
      if (depth === 0) return app.slice(start, cursor + 1);
    }
  }
  assert.fail(`unbalanced braces extracting ${name}`);
}

function buildContext(initialStorage = {}) {
  const store = new Map(Object.entries(initialStorage));
  const rows = [
    { session_id: 'origin', modified: 1 },
    { session_id: 'head', continued_from_session_id: 'origin', modified: 2 },
  ];
  const ctx = {
    localStorage: {
      getItem(key) { return store.has(key) ? store.get(key) : null; },
      setItem(key, value) { store.set(key, String(value)); },
      removeItem(key) { store.delete(key); },
    },
    conversationsData: rows,
    archiveData: [],
    _continuationFoldedCounts: new Map(),
    _continuationFoldedAncestors: new Map(),
    continuationParentId(row) {
      return String((row && row.continued_from_session_id) || '').trim();
    },
    _continuationRowId(row) {
      return String((row && (row.session_id || row.id)) || '').trim();
    },
    appearanceRefreshes: 0,
    spawnRefreshes: 0,
    sidebarRenders: 0,
  };
  ctx.refreshAppearanceChecks = () => { ctx.appearanceRefreshes += 1; };
  ctx.refreshSpawnEngineValue = () => { ctx.spawnRefreshes += 1; };
  ctx.renderSidebar = (renderedRows) => {
    assert.equal(renderedRows, rows);
    ctx.sidebarRenders += 1;
  };
  vm.createContext(ctx);
  const storageKeyDecl = app.match(
    /const CONTINUATION_FOLDING_STORAGE_KEY = 'ccc-fold-continuation-chains';/,
  );
  assert.ok(storageKeyDecl, 'continuation folding storage key declaration is missing');
  vm.runInContext(
    storageKeyDecl[0]
      + extractFunction('getContinuationFoldingPref')
      + extractFunction('setContinuationFoldingPref')
      + extractFunction('settingsResetSessions')
      + extractFunction('_foldContinuationAncestorRows')
      + '; this.getPref = getContinuationFoldingPref;'
      + ' this.setPref = setContinuationFoldingPref;'
      + ' this.resetSessions = settingsResetSessions;'
      + ' this.fold = _foldContinuationAncestorRows;',
    ctx,
  );
  return { ctx, rows, store };
}

test('continuation folding is off when the browser has no saved preference', () => {
  const { ctx, rows } = buildContext();

  assert.equal(ctx.getPref(), false);
  assert.deepEqual(Array.from(ctx.fold(rows), row => row.session_id), ['origin', 'head']);
});

test('the opt-in preserves the existing newest-row continuation fold', () => {
  const { ctx, rows } = buildContext({ 'ccc-fold-continuation-chains': 'on' });

  assert.equal(ctx.getPref(), true);
  assert.deepEqual(Array.from(ctx.fold(rows), row => row.session_id), ['head']);
});

test('Sessions & Spawning exposes the browser-local folding switch', () => {
  assert.match(html, /id="settingsContinuationFoldingToggle"/);
  assert.match(html, /data-continuation-folding-toggle/);
  assert.match(html, /Hide earlier continued sessions/);
  assert.match(html, /Active, Coding, Workers, and Archived/);
});

test('applying the preference persists both states and rerenders immediately', () => {
  const { ctx, store } = buildContext();

  ctx.setPref(true);
  assert.equal(store.get('ccc-fold-continuation-chains'), 'on');
  assert.equal(ctx.getPref(), true);
  assert.equal(ctx.appearanceRefreshes, 1);
  assert.equal(ctx.sidebarRenders, 1);

  ctx.setPref(false);
  assert.equal(store.has('ccc-fold-continuation-chains'), false);
  assert.equal(ctx.getPref(), false);
  assert.equal(ctx.appearanceRefreshes, 2);
  assert.equal(ctx.sidebarRenders, 2);
});

test('Reset Sessions removes the opt-in and repaints through the same helper', () => {
  const { ctx, store } = buildContext({
    'ccc-fold-continuation-chains': 'on',
    'ccc.spawnEngine': 'codex',
    'ccc-spawn-cwd': '/tmp/repo',
  });

  ctx.resetSessions();

  assert.equal(store.has('ccc-fold-continuation-chains'), false);
  assert.equal(store.has('ccc.spawnEngine'), false);
  assert.equal(store.has('ccc-spawn-cwd'), false);
  assert.equal(ctx.getPref(), false);
  assert.equal(ctx.spawnRefreshes, 1);
  assert.equal(ctx.appearanceRefreshes, 1);
  assert.equal(ctx.sidebarRenders, 1);
});

test('the delegated Settings click routes through the behavior helper', () => {
  assert.match(app, /e\.target\.closest\('\[data-continuation-folding-toggle\]'\)/);
  assert.match(app, /setContinuationFoldingPref\(!getContinuationFoldingPref\(\)\)/);
});
