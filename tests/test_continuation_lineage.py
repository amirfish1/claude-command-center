import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestContinuationLineage(unittest.TestCase):
    def test_sidebar_flips_recorded_f2_continuations(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function f2EffectiveParentSessionId(sessionId, recordedParentId)", app_js)
        self.assertIn("const _continuationEdges = _f2ContinuationEdges();", app_js)
        self.assertIn("if (newestContinuation && newestContinuation !== sid) return newestContinuation;", app_js)
        self.assertIn("const _subagentRowParentId = (c) => f2EffectiveParentSessionId(", app_js)
        self.assertIn("const _currentSessionParentId = (c) => f2EffectiveParentSessionId(", app_js)
        self.assertIn("const _allTabParentId = (c) => f2EffectiveParentSessionId(", app_js)


if __name__ == "__main__":
    unittest.main()
