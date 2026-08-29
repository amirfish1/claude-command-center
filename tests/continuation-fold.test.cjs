// Continuation chains (F2 "Continue in a new session" / auto-resume) render
// as ONE row: the origin folds into its successor, the successor's always-on
// ⤴ from: chip opens the origin, and lanes the origin spawned re-home to the
// chain head. The old opt-in "Hide earlier continued sessions" switch is gone:
// folding is unconditional now.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('static/app.js', 'utf8');
const html = fs.readFileSync('static/index.html', 'utf8');
const css = fs.readFileSync('static/app.css', 'utf8');

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

test('the continuation fold preference and its Settings switch are gone', () => {
  assert.equal(app.includes('ccc-fold-continuation-chains'), false);
  assert.equal(app.includes('getContinuationFoldingPref'), false);
  assert.equal(app.includes('_foldContinuationAncestorRows'), false);
  assert.equal(html.includes('settingsContinuationFoldingToggle'), false);
  assert.equal(html.includes('Hide earlier continued sessions'), false);
});

test('both tree builders fold the origin into its successor and re-home its lanes', () => {
  assert.equal(app.includes('_isContinuationPair'), false);
  assert.equal((app.match(/const fold = _continuationFoldMaps\(rows, /g) || []).length, 2);
  assert.equal((app.match(/if \(!id \|\| fold\.hidden\.has\(id\)\) return;/g) || []).length, 2);
  assert.equal((app.match(/const pid = fold\.headOf\(/g) || []).length, 2);
});

test('_continuationFoldMaps hides the origin and points its lanes at the chain head', () => {
  const ctx = { continuationParentId: (c) => String((c && c.continued_from_session_id) || '') };
  vm.createContext(ctx);
  vm.runInContext(extractFunction('_continuationFoldMaps') + '; this.fold = _continuationFoldMaps;', ctx);
  const idOf = (c) => c.session_id;
  const rows = [
    { session_id: 'origin-session-01', modified: 1 },
    { session_id: 'mid-session-0001', continued_from_session_id: 'origin-session-01', modified: 2 },
    { session_id: 'head-session-001', continued_from_session_id: 'mid-session-0001', modified: 3 },
    { session_id: 'lane-session-001', parent_session_id: 'origin-session-01', modified: 2 },
  ];
  const fold = ctx.fold(rows, idOf);
  assert.deepEqual([...fold.hidden].sort(), ['mid-session-0001', 'origin-session-01']);
  assert.equal(fold.headOf('origin-session-01'), 'head-session-001');
  assert.equal(fold.headOf('lane-session-001'), 'lane-session-001');
  // A successor outside the list does not fold its origin.
  const alone = ctx.fold(rows.slice(0, 1), idOf);
  assert.equal(alone.hidden.size, 0);
});

test('the successor is the effective parent of the session it continued from', () => {
  const ctx = {
    conversationsData: [
      { session_id: 'origin-session-01', modified: 1 },
      { session_id: 'successor-sess-01', first_message: 'Origin session id: origin-session-01', modified: 2 },
    ],
    localStorage: { getItem() { return null; } },
    manualSubsessionParentId() { return ''; },
  };
  vm.createContext(ctx);
  vm.runInContext(
    'let _f2ContinuationEdgesCache = { raw: null, edges: {} };'
      + extractFunction('_f2ContinuationEdges')
      + extractFunction('f2EffectiveParentSessionId')
      + '; this.eff = f2EffectiveParentSessionId;',
    ctx,
  );
  assert.equal(ctx.eff('origin-session-01', ''), 'successor-sess-01');
  assert.equal(ctx.eff('successor-sess-01', 'origin-session-01'), '');
});

test('the ⤴ from: chip survives the hover overlay like the parent chip does', () => {
  assert.match(css, /\.conv-item:hover:not\(\.active\) \.conv-hover-meta-row \.conv-session-origin-chip\.is-successor,/);
  assert.match(css, /\.conv-item:hover:not\(\.active\) \.conv-hover-meta-row:has\(\.conv-session-origin-chip\.is-successor\),/);
});

test('the selected-row title no longer carries ⤴ from: chips', () => {
  assert.equal(app.includes('continuationChipsHtml'), false);
  assert.equal(app.includes('data-continuation-sid'), false);
  assert.equal(css.includes('.conv-session-origin-chip.is-continuation'), false);
});

test('a continuation is not counted as a lane of its origin; the head carries the chain lanes', () => {
  assert.match(app, /if \(!pid \|\| continuationParentId\(row\) === pid\) return;/);
  assert.match(app, /subagentClusterMeta \? 0 : _sessionLaneCountWithAncestors\(c\)/);
});
