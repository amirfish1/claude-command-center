"""The cache-miss callout must stay quiet on healthy, mostly-cached turns."""

import json
import pathlib
import subprocess
import textwrap
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestCacheMissCalloutThreshold(unittest.TestCase):
    def _callout(self, cases):
        """Run _cacheMissCallout from app.js over (tIn, tCached) pairs."""
        node_program = textwrap.dedent(
            r"""
            const fs = require('fs');
            const source = fs.readFileSync('static/app.js', 'utf8');
            const pick = (name, endMarker) => {
              const start = source.indexOf(name);
              const end = source.indexOf(endMarker, start);
              if (start < 0 || end < 0) throw new Error('helper not found: ' + name);
              return source.slice(start, end);
            };
            eval(pick('function _formatTokensAntigravity', 'function _formatAntigravityTokenChips'));
            eval(pick('const CACHE_MISS_CALLOUT_MIN_SHARE', 'function _getCtxLimitOverride'));
            const cases = JSON.parse(process.argv[1]);
            console.log(JSON.stringify(cases.map(([tIn, tCached]) =>
              _cacheMissCallout(tIn, tCached))));
            """
        )
        out = subprocess.run(
            ["node", "-e", node_program, "--", json.dumps(cases)],
            cwd=PROJECT_ROOT, check=True, capture_output=True, text=True,
        )
        return json.loads(out.stdout)

    def test_mostly_cached_turn_stays_quiet(self):
        # The screenshot that started this: 97.2k in / 96.4k cached is a 99%
        # cache hit. Every turn has a small uncached tail (the new user
        # message, a tool result) — firing on any delta at all trains people
        # to ignore the callout.
        quiet, loud = self._callout([[97_200, 96_400], [97_200, 20_000]])
        self.assertEqual(quiet, "")
        self.assertTrue(loud.startswith("CACHE MISS"), loud)

    def test_threshold_is_seventy_percent_of_input(self):
        # 100k in: cached 30k leaves a 70.0% miss (not above the bar, quiet);
        # cached 29k leaves 71% (fires).
        at_bar, over_bar = self._callout([[100_000, 30_000], [100_000, 29_000]])
        self.assertEqual(at_bar, "")
        self.assertEqual(over_bar, "CACHE MISS — 71.0k input tokens uncached")

    def test_fully_cold_turn_still_fires(self):
        cold, empty = self._callout([[50_000, 0], [0, 0]])
        self.assertEqual(cold, "CACHE MISS — 50.0k input tokens uncached")
        self.assertEqual(empty, "")


if __name__ == "__main__":
    unittest.main()
