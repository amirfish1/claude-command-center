const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { loadRowOutcomeView } = require('./row_outcome_view_harness.cjs');
const app = require('./legacy-dashboard-source.cjs');
const cacheCode = app.slice(app.indexOf('  const ARCHIVE_SIDEBAR_CACHE_KEY'),
  app.indexOf('  // Dedupe concurrent /api/conversations/list'));

function cacheRows(rows) {
  const storage = new Map();
  const context = vm.createContext({ localStorage: {
    setItem: (k, v) => storage.set(k, v), getItem: k => storage.get(k),
    removeItem: k => storage.delete(k),
  } });
  vm.runInContext(cacheCode + '\nthis.roundTrip = rows => { _writeArchiveSidebarCache(rows, "7d"); return _readArchiveSidebarCache("7d"); };', context);
  return context.roundTrip(rows);
}

test('the bounded startup preview keeps a late parent with its child', () => {
  const child = { id: 'child', parent_session_id: 'parent', idea_ticket: { ref: 'TEST-1' } };
  const fleet = Array.from({ length: 600 }, (_, i) => ({ id: 'worker-' + i }));
  const out = cacheRows([child, ...fleet, { id: 'parent', parent_session_id: 'root' }, { id: 'root' }]);
  assert.equal(out.length, 500);
  const ids = Array.from(out, row => row.id);
  for (const id of ['child', 'parent', 'root']) assert.ok(ids.includes(id));
  assert.equal(new Set(ids).size, ids.length);
});

test('parent batches obey the character budget and tolerate cyclic lineage', () => {
  const out = cacheRows([
    { id: 'child', parent_session_id: 'huge' },
    { id: 'huge', folder_path: 'x'.repeat(260 * 1024) },
    { id: 'a', parent_session_id: 'b' }, { id: 'b', parent_session_id: 'a' },
  ]);
  assert.deepEqual(Array.from(out, row => row.id), ['a', 'b']);
});

test('a metadata-only preview reports loading until the real completed status arrives', () => {
  const rowOutcome = loadRowOutcomeView();
  const [preview] = cacheRows([{ id: 'lane', parent_session_id: 'parent',
    session_state: { did: 'Old update', next_step_user: 'Old question' },
    last_assistant_text: 'Old answer', needs_approval: true }]);
  const status = rowOutcome(preview, { childLane: true });
  assert.equal(status.label, 'Loading');
  assert.equal(status.text, 'Loading session status…');
  assert.equal(status.ready, false);
  assert.equal(preview.session_state, undefined);
  assert.equal(preview.needs_approval, undefined);
  const fresh = { ...preview, last_event_type: 'assistant', session_state: {
    did: 'Verified and reported the corrected behavior.', next_step_user: 'None',
    completion: { version: 1, valid: true, ready_to_close: true },
  } };
  assert.equal(rowOutcome(fresh, { childLane: true }).ready, true);
  assert.equal(rowOutcome(fresh, { childLane: true }).offerClose, false);
  assert.equal(rowOutcome(fresh, { childLane: true }).text, fresh.session_state.did);
  assert.equal(rowOutcome({ ...preview, state: 'working' }).label, 'Working');
});


test('the rendered desktop outcome includes the loading message without a footer or reply', () => {
  const start = app.indexOf('      // Outcome line (GOAL-1)');
  const end = app.indexOf('      const _outcomeInline =', start);
  const row = { id: 'preview', _statusLoading: true };
  const html = vm.runInNewContext('(function () {' + app.slice(start, end) + '\nreturn outcomeHtml; })()', {
    c: row, _outView: loadRowOutcomeView()(row), _readyToCloseRow: false,
    _childLaneRow: true, isBacklogRow: false, isGithubPrRow: false,
    escapeHtml: String, escapeAttr: String,
    rowHandoff: () => null, sessionAttentionStep: () => null, attentionChipsHtml: () => '',
  });
  assert.match(html, /data-label="Loading"/);
  assert.match(html, /Loading session status…/);
  assert.doesNotMatch(html, /conv-ready-close|conv-quick-action/);
});

test('a standalone lane has parent navigation on desktop and phone', () => {
  const start = app.indexOf('      const waParentHtml =');
  const end = app.indexOf('      const waHtml =', start);
  for (const standaloneChild of [false, true]) {
    const html = vm.runInNewContext(app.slice(start, end) + '\nwaParentHtml;', {
      standaloneChild, recordedParent: 'origin',
      spawnParentVisibleId: () => 'continuation', escapeAttr: String,
    });
    if (!standaloneChild) assert.equal(html, '');
    else {
      assert.match(html, /class="conv-family-link" data-parent-sid="continuation"/);
      assert.match(html, />Open parent<\/button>/);
    }
  }
  const desktop = app.slice(app.indexOf("+ '<span class=\"conv-meta-col\">'"),
    app.indexOf('+ earlierDetailsHtml', app.indexOf("+ '<span class=\"conv-meta-col\">'")));
  assert.match(desktop, /\+ waParentHtml/);
  assert.match(app, /'<div class="wa-row-footer">' \+ sessionIdChipHtml \+ waParentHtml/);
});
