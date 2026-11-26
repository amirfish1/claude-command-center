// The 15 s queue-meta poll repaints the sidebar only when the chip signature
// changes, through the frame-coalesced scheduler (CCC-697).
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { functionSource } = require('./conversation-source.cjs');

function harness() {
  const scheduled = [];
  let sig = 'a';
  const sharedState = { UX_FIXES_QUEUE_META_TTL_MS: 15000, _uxFixesQueueMetaLoadedAt: 0, uxFixesQueueMeta: null,
    _uxFixesChipPaintedSig: '', _uxFixesQueueMetaPromise: null };
  const ctx = {
    sharedState, hostServices: {}, Date,
    _dashboardCoveredByApp: () => false,
    _queueListFetch: async () => ({ items: [] }),
    _setUxFixesQueueMeta: () => (sharedState.uxFixesQueueMeta = { _sig: sig }),
    _scheduleSidebarRender: opts => scheduled.push(opts),
    _renderArchiveIfLoaded: () => { throw new Error('direct archive render'); },
  };
  vm.createContext(ctx);
  vm.runInContext(functionSource('refreshUxFixesQueueMeta'), ctx);
  return { ctx, scheduled, setSig: v => { sig = v; } };
}

test('queue meta poll repaints once per signature change and not for an unchanged queue', async () => {
  const h = harness();
  await h.ctx.refreshUxFixesQueueMeta({ force: true });
  assert.equal(h.scheduled.length, 1);
  assert.equal(h.scheduled[0].lifecycle, true);
  await h.ctx.refreshUxFixesQueueMeta({ force: true });
  await h.ctx.refreshUxFixesQueueMeta({ force: true });
  assert.equal(h.scheduled.length, 1, 'an unchanged queue does not rebuild the sidebar');
  h.setSig('b');
  await h.ctx.refreshUxFixesQueueMeta({ force: true });
  assert.equal(h.scheduled.length, 2);
  h.setSig('c');
  await h.ctx.refreshUxFixesQueueMeta({ force: true, render: false });
  assert.equal(h.scheduled.length, 2, 'render:false never paints');
});
