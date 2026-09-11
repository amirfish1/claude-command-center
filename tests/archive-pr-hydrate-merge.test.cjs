// Regression: the PR-hydrate pass fetches the include_prs cache variant,
// whose server snapshot lags the base variant (separate cache key, slow
// detached rebuild — during the 2026-08-05 incident it froze for 1h+).
// Wholesale-replacing archiveData with that older payload made sessions
// vanish and live counts regress, then snap back on the next base poll
// (the conv-list "flicker"). The hydrate pass must graft PR enrichment
// onto the fresher current rows, never replace them.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('static/app.js', 'utf8');

function extractFunction(name) {
  const start = app.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `function ${name} not found in app.js`);
  let i = app.indexOf('{', start);
  let depth = 0;
  for (; i < app.length; i++) {
    if (app[i] === '{') depth++;
    else if (app[i] === '}') {
      depth--;
      if (depth === 0) return app.slice(start, i + 1);
    }
  }
  assert.fail(`unbalanced braces extracting ${name}`);
}

const ctx = vm.createContext({});
vm.runInContext(extractFunction('_archiveRowStableKey'), ctx);
vm.runInContext(extractFunction('_graftArchivePrEnrichment'), ctx);
const graft = ctx._graftArchivePrEnrichment;

const current = [
  { session_id: 'new-1', mtime: 2000, state: 'live', live_context_percent: 24 },
  { session_id: 'old-1', mtime: 1000, state: 'live', tail_pr_number: 7, pr_state: null },
];
// Stale enriched snapshot: missing new-1 entirely, has older live fields for
// old-1, plus the PR enrichment and a standalone github_pr row.
const enriched = [
  {
    session_id: 'old-1', mtime: 900, state: 'ended', live_context_percent: 90,
    tail_pr_number: 7, pr_state: 'open', pr_review_decision: 'APPROVED',
    effective_branch: 'feat/x', worktree_dirty: false,
  },
  { id: 'pr-row', source: 'github_pr', tail_pr_url: 'https://github.com/a/b/pull/7' },
];

test('sessions present in current never vanish when enriched payload is staler', () => {
  const out = graft(current, enriched);
  const keys = out.map(r => r.session_id || r.id);
  assert.ok(keys.includes('new-1'), 'session missing from stale enriched snapshot was dropped');
  assert.ok(keys.includes('old-1'));
});

test('current (fresher) liveness fields win over enriched snapshot', () => {
  const out = graft(current, enriched);
  const old1 = out.find(r => r.session_id === 'old-1');
  assert.equal(old1.mtime, 1000);
  assert.equal(old1.state, 'live');
});

test('PR/effective/worktree enrichment is grafted onto current rows', () => {
  const out = graft(current, enriched);
  const old1 = out.find(r => r.session_id === 'old-1');
  assert.equal(old1.pr_state, 'open');
  assert.equal(old1.pr_review_decision, 'APPROVED');
  assert.equal(old1.effective_branch, 'feat/x');
  assert.equal(old1.worktree_dirty, false);
});

test('standalone github_pr rows from the enriched payload are appended', () => {
  const out = graft(current, enriched);
  assert.ok(out.some(r => r.source === 'github_pr'));
});

test('empty current falls back to the enriched payload', () => {
  const out = graft([], enriched);
  assert.equal(out.length, enriched.length);
});

test('enrichment never nulls out fresher non-empty values', () => {
  const out = graft(
    [{ session_id: 's', tail_pr_number: 9 }],
    [{ session_id: 's', tail_pr_number: null, pr_state: null }],
  );
  assert.equal(out[0].tail_pr_number, 9);
});

test('hydrate pass no longer wholesale-replaces archiveData', () => {
  const hydrateStart = app.indexOf('function _hydrateArchivePrData(');
  assert.notEqual(hydrateStart, -1);
  const body = app.slice(hydrateStart, hydrateStart + 2000);
  assert.ok(!/archiveData\s*=\s*convs\b/.test(body),
    '_hydrateArchivePrData must graft enrichment, not replace archiveData');
  assert.match(body, /_graftArchivePrEnrichment\(/);
});
