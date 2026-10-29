const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync('static/app.js', 'utf8');
const start = app.indexOf('  function _insertLoadEarlierBanner(');
const end = app.indexOf('\n  // Codex rollouts', start);

for (const fails of [false, true]) {
  test(`history ${fails ? 'failure can be retried' : 'success cleans up without a paint callback'}`, async () => {
    let click, calls = 0, removed = 0;
    const banner = { textContent: '', addEventListener(type, fn) { if (type === 'click') click = fn; } };
    const pane = { conversationId: 'test-session', firstLine: 200 };
    const view = { querySelector() { return null; }, insertBefore() {} };
    const ctx = {
      document: { createElement() { return banner; } }, window: {},
      _ensureLoadEarlierStyle() {}, paneByPaneId() { return pane; },
      _topVisibleAnchor() { return null; }, _restoreAnchor() {},
      _contNextParentForPane() { return ''; }, requestAnimationFrame() {},
      _showConvLoading() { return { remove() { removed++; } }; },
      async fetchConversationEvents() {
        if (fails && ++calls === 1) throw new Error('History unavailable');
      },
    };
    vm.runInNewContext(app.slice(start, end) + '\n_insertLoadEarlierBanner(view, "test-session", "pane");', { ...ctx, view });
    await click();
    assert.equal(banner.disabled, false);
    assert.equal(removed, 1);
    if (fails) {
      assert.match(banner.textContent, /Retry/);
      assert.equal(banner.title, 'History unavailable');
      await click();
      assert.equal(calls, 2);
      assert.equal(removed, 2);
    }
    assert.equal(banner.textContent, '↑ Load earlier messages');
  });
}
