"""Regression coverage for close vs. stale Queue-list responses."""

import json
import pathlib
import subprocess
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestQueueCloseRefreshRace(unittest.TestCase):
    def test_stale_queue_render_restarts_from_the_canonical_cache(self):
        """A renderer paused on health data cannot paint an older ticket snapshot."""
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        render_start = app_js.index("function _renderQueuePanel(options)")
        render_end = app_js.index("// Jump the conversation pane", render_start)
        render = app_js[render_start:render_end]

        self.assertIn("const renderVersion = _uxqItemsVersion;", render)
        self.assertIn(
            "if (renderVersion !== _uxqItemsVersion) return _renderQueuePanel({ allowStale: true });",
            render,
        )

    def test_close_response_wins_over_an_older_inflight_list_response(self):
        """An acknowledged close must not be reverted by a pre-close list read."""
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("let _uxqItemsVersion = 0;", app_js)

        close_start = app_js.index("const doMarkClosed = async () =>")
        close_end = app_js.index("markClosedBtn.addEventListener", close_start)
        close_handler = app_js[close_start:close_end]
        self.assertIn("if (_uxqReplaceCachedItem(d.item))", close_handler)
        self.assertIn("_renderQueuePanel({ allowStale: true });", close_handler)

        fetch_start = app_js.index("let _uxqItemsCache = { ts: 0, items: [], syncedAt: 0 };")
        fetch_end = app_js.index("// Per-project queue-health snapshot", fetch_start)
        fetch_code = app_js[fetch_start:fetch_end]
        item_ref_start = app_js.index("function _uxqItemRef(item)")
        item_ref_end = app_js.index("function _uxqItemForRef", item_ref_start)
        replace_start = app_js.index("function _uxqReplaceCachedItem(item)")
        replace_end = app_js.index("async function _uxqOpenItemDetail", replace_start)

        node_program = """
const vm = require('vm');
let resolveOlder;
let calls = 0;
const metadataSnapshots = [];
const sidebarRenders = [];
const older = new Promise(resolve => { resolveOlder = resolve; });
const context = {
  Date, Set, Map, Promise,
  // Sidebar/visibility dependencies live outside the extracted queue code.
  uxFixesQueueMeta: { _sig: '' },
  _queueSnapshotIsForeground: () => false,
  _setUxFixesQueueMeta: items => {
    metadataSnapshots.push(items.map(item => ({ ...item })));
    return context.uxFixesQueueMeta = { _sig: JSON.stringify(items) };
  },
  _scheduleSidebarRender: options => sidebarRenders.push(options),
  backgroundApiFetch: async () => {
    calls += 1;
    if (calls === 1) return older;
    return { json: async () => ({ items: [{ ref: 'CCC-9', status: 'closed' }] }) };
  },
};
vm.createContext(context);
vm.runInContext(%s + %s + %s + `
globalThis.queueRace = {
  fetch: _fetchUxqItems,
  replace: _uxqReplaceCachedItem,
  seed(items) { _uxqItemsCache = { ts: Date.now(), items }; },
  expire() { _uxqItemsCache.ts = 0; },
  items() { return _uxqItemsCache.items; },
};
`, context);
(async () => {
  context.queueRace.seed([{ ref: 'CCC-9', status: 'open' }]);
  context.queueRace.expire();
  const inflight = context.queueRace.fetch(false);
  if (calls !== 1) throw new Error('older list request never started');
  if (!context.queueRace.replace({ ref: 'CCC-9', status: 'closed' })) throw new Error('close did not replace cached ticket');
  if (metadataSnapshots.length !== 1 || metadataSnapshots[0][0].status !== 'closed') throw new Error('close did not sync sidebar metadata');
  resolveOlder({ json: async () => ({ items: [{ ref: 'CCC-9', status: 'open' }] }) });
  await inflight;
  if (context.queueRace.items()[0].status !== 'closed') throw new Error('older response reverted close');
  if (metadataSnapshots.length !== 1) throw new Error('older response overwrote sidebar metadata');
  context.queueRace.expire();
  await context.queueRace.fetch(false);
  if (calls !== 2) throw new Error('fresh list request never started');
  if (context.queueRace.items()[0].status !== 'closed') throw new Error('fresh response did not preserve close');
  if (metadataSnapshots.length !== 2 || metadataSnapshots[1][0].status !== 'closed') throw new Error('fresh response did not sync sidebar metadata');
  if (sidebarRenders.length !== 1 || !sidebarRenders[0].lifecycle) throw new Error('sidebar should repaint only when metadata changes');
})().catch(err => { console.error(err); process.exit(1); });
""" % (json.dumps(app_js[item_ref_start:item_ref_end]), json.dumps(fetch_code), json.dumps(app_js[replace_start:replace_end]))

        subprocess.run(["node", "-e", node_program], cwd=PROJECT_ROOT, check=True)


if __name__ == "__main__":
    unittest.main()
