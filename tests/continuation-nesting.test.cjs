// Continuation chains (F2 "Continue in a new session" / auto-resume) render
// as ONE unit: the successor is the row, the ancestor nests under it as a
// child row. The old opt-in "Hide earlier continued sessions" fold is gone.
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

test('tree builders no longer refuse to nest a continuation pair', () => {
  assert.equal(app.includes('_isContinuationPair'), false);
  // Both the Current-sessions and All-tab builders keep the plain parent walk.
  const nestLine = /if \(!id \|\| !pid \|\| id === pid \|\| !byId\.has\(pid\)\) return;\n\s*childIds\.add\(id\);/g;
  assert.equal((app.match(nestLine) || []).length, 2);
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

test('continuation ancestors ride in the cluster as real rows, expanded by default', () => {
  assert.match(app, /continuation: continuationIds\.has\(id\)/);
  assert.match(app, /subagentCompact: !entry\.continuation/);
  assert.match(app, /continuationAncestor: !!entry\.continuation/);
  assert.match(app, /!_subagentExpandedParents\.has\('collapsed:' \+ parentId\)/);
  assert.match(app, /data-continuation-cluster="1"/);
});

test('the selected-row title no longer carries ⤴ from: chips', () => {
  assert.equal(app.includes('continuationChipsHtml'), false);
  assert.equal(app.includes('data-continuation-sid'), false);
  assert.equal(css.includes('.conv-session-origin-chip.is-continuation'), false);
});

test('a nested continuation ancestor does not show its own lane badge', () => {
  assert.match(app, /\(subagentClusterMeta \|\| opts\.continuationAncestor\) \? 0 :/);
});
