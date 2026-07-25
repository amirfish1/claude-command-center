"""Regression tests for _codex_custom_tool_arg.

The original regex used a tempered-dot body — `(?:\\.|(?!(?P=quote)).)*` —
whose branches both accepted a backslash, so an escape-heavy string literal
that never closes (Codex truncates long tool-call previews mid-string in the
rollout) sent re.search into exponential backtracking. In production that
pinned a /api/codex/stuck-summary request thread at 100% CPU and made the
whole server unresponsive. The fix keeps the branches disjoint
(`\\.` vs `[^"\\]`) with possessive quantifiers, so matching is linear.
"""

import time
import unittest

import server


class CodexCustomToolArgTests(unittest.TestCase):
    def test_extracts_double_quoted_arg(self):
        src = 'const r = await tools.exec_command({ cmd: "ls -la", sandbox: "x" })'
        self.assertEqual(server._codex_custom_tool_arg(src, "cmd"), "ls -la")

    def test_extracts_json_style_quoted_key(self):
        src = 'tools.exec_command({"cmd":"echo hi"})'
        self.assertEqual(server._codex_custom_tool_arg(src, "cmd"), "echo hi")

    def test_extracts_single_quoted_arg(self):
        src = "tools.exec_command({ cmd: 'single quoted' })"
        self.assertEqual(server._codex_custom_tool_arg(src, "cmd"), "single quoted")

    def test_handles_escaped_quotes_in_body(self):
        src = '({"cmd":"with \\"escaped\\" quotes"})'
        self.assertEqual(
            server._codex_custom_tool_arg(src, "cmd"), 'with "escaped" quotes'
        )

    def test_handles_trailing_escaped_backslashes(self):
        src = '({"cmd":"foo\\\\"})'
        self.assertEqual(server._codex_custom_tool_arg(src, "cmd"), "foo\\")

    def test_missing_key_returns_empty(self):
        self.assertEqual(server._codex_custom_tool_arg("no key here", "cmd"), "")

    def test_unclosed_escape_heavy_literal_returns_fast(self):
        # The production shape: a truncated `git diff | sed -n '/^@@ -1\|...'`
        # command — thousands of backslash escapes, no closing quote. Must
        # return "" in linear time, not hang in regex backtracking.
        evil = '({"cmd":"' + "\\|" * 5000
        start = time.monotonic()
        result = server._codex_custom_tool_arg(evil, "cmd")
        elapsed = time.monotonic() - start
        self.assertEqual(result, "")
        self.assertLess(elapsed, 1.0, "regex backtracking regression: %.3fs" % elapsed)

    def test_unclosed_double_backslash_literal_returns_fast(self):
        evil = 'tools.exec_command({ cmd: "' + "\\\\" * 5000 + "x"
        start = time.monotonic()
        result = server._codex_custom_tool_arg(evil, "cmd")
        elapsed = time.monotonic() - start
        self.assertEqual(result, "")
        self.assertLess(elapsed, 1.0, "regex backtracking regression: %.3fs" % elapsed)


if __name__ == "__main__":
    unittest.main()
