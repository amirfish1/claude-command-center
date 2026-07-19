import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestQueueTicketEditRefresh(unittest.TestCase):
    def test_edit_response_replaces_the_cached_row_before_repaint(self):
        """Saving a ticket field must repaint its row from the edit response."""
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        helper_start = app_js.index("function _uxqReplaceCachedItem(item)")
        helper_end = app_js.index("async function _uxqOpenItemDetail", helper_start)
        helper = app_js[helper_start:helper_end]
        self.assertIn("freshItems[index] = item;", helper)
        self.assertIn("_uxqItemsCache = { ts: Date.now(), items: freshItems };", helper)

        save_start = app_js.index("async function _uxqSaveField(ref, field, value)")
        save_end = app_js.index("function _uxqRelTime", save_start)
        save = app_js[save_start:save_end]
        self.assertIn("if (_uxqReplaceCachedItem(d.item))", save)
        self.assertIn("_renderQueuePanel({ allowStale: true });", save)


if __name__ == "__main__":
    unittest.main()
