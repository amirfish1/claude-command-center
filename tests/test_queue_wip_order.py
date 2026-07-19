"""Regression coverage for the Queue panel's top-level ticket ordering."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestQueueWipOrder(unittest.TestCase):
    def test_all_history_keeps_the_same_operational_bucket_order_as_open(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        queue_render = app_js[
            app_js.index("function _renderQueuePanel(options)"):
            app_js.index("// Jump the conversation pane", app_js.index("function _renderQueuePanel(options)"))
        ]

        self.assertIn("const historyOrder = _uxqGetFilter() === 'all';", queue_render)
        self.assertIn("const _operationalBucket = it =>", queue_render)
        self.assertIn("const bucket = _operationalBucket(a) - _operationalBucket(b);", queue_render)
        self.assertNotIn("_uxqCreatedAtMs(b) - _uxqCreatedAtMs(a)", queue_render)

    def test_ticket_buckets_are_wip_input_unresolved_drain_clean_closed_then_inert(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn(
            "const _operationalBucket = it => {",
            app_js,
        )
        self.assertIn("if (_isLiveWip(it)) return 0;", app_js)
        self.assertIn("if (status === 'blocked') return 1;", app_js)
        self.assertIn("if (_hasUnresolved(it)) return 2;", app_js)
        self.assertIn("if (_isWaitingToDrain(it)) return 3;", app_js)
        self.assertIn("if (status === 'closed') return 4;", app_js)
        self.assertIn("return 5;", app_js)

    def test_only_a_live_worker_backed_claim_is_animated_as_wip(self):
        """Historical claim metadata must not make an open row look live."""
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        queue_render = app_js[
            app_js.index("function _renderQueuePanel(options)"):
            app_js.index("// Jump the conversation pane", app_js.index("function _renderQueuePanel(options)"))
        ]

        self.assertIn("const _liveWorkers =", queue_render)
        self.assertIn("worker.alive !== false", queue_render)
        self.assertIn("const _hasLiveClaim = it =>", queue_render)
        self.assertIn("rawStatus === 'open' && _hasLiveClaim(it)", queue_render)
        self.assertIn("rawStatus === 'in_progress'", queue_render)
        self.assertIn("if (rawStatus === 'in_progress' && _isStaleClaim(it)) return 'open';", queue_render)
        self.assertIn("is-stale-claim", queue_render)
        self.assertIn("stale claim - no current live worker", queue_render)
        self.assertIn(": staleClaim", queue_render)

    def test_live_claim_status_uses_a_conspicuous_blue_marker(self):
        """A live claim must remain visibly distinct from an unclaimed row."""
        app_css = (PROJECT_ROOT / "static" / "app.css").read_text(encoding="utf-8")

        live_claim_css = app_css[
            app_css.index(".fq-row.is-in_progress .fq-status {"):
            app_css.index(".fq-row.is-in_progress.is-stale-claim", app_css.index(".fq-row.is-in_progress .fq-status {"))
        ]

        self.assertIn("background: var(--accent, #58a6ff);", live_claim_css)

    def test_newly_fetched_queue_items_get_a_temporary_highlight(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        app_css = (PROJECT_ROOT / "static" / "app.css").read_text(encoding="utf-8")

        self.assertIn("const _UXQ_NEW_ITEM_GLOW_MS = 4500;", app_js)
        self.assertIn("_uxqNewItemExpires", app_js)
        self.assertIn("fq-new-item", app_js)
        self.assertIn(".fq-row.fq-new-item", app_css)
        self.assertIn("@keyframes fq-new-item-glow", app_css)


if __name__ == "__main__":
    unittest.main()
