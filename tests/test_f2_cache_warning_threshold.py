"""The stale-session continuation warning should reach a full 200k context."""

import json
import pathlib
import subprocess
import textwrap
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestF2CacheWarningThreshold(unittest.TestCase):
    def test_stale_two_hundred_k_claude_session_offers_continuation(self):
        """A 200k session is expensive enough to warrant the cache warning."""
        program = textwrap.dedent(
            r"""
            const fs = require('fs');
            const source = fs.readFileSync('static/app.js', 'utf8');
            const pick = (startMarker, endMarker) => {
              const start = source.indexOf(startMarker);
              const end = source.indexOf(endMarker, start);
              if (start < 0 || end < 0) throw new Error('helper not found');
              return source.slice(start, end);
            };
            eval(pick('function _contextFieldsFromRow', '// ── F2 cold-session composer'));
            eval(pick('const F2_TOKEN_THRESHOLD', 'function f2ResolveSpawnCwd'));
            const gate = f2ResumeGate({
              sid: 'stale-200k',
              session: { source: 'claude', model: 'opus-5' },
              row: {
                latest_input_tokens: 200000,
                modified: Date.now() / 1000 - 61 * 60,
              },
            });
            console.log(JSON.stringify(gate));
            """
        )
        result = subprocess.run(
            ["node", "-e", program],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        gate = json.loads(result.stdout)
        self.assertIsNotNone(gate)
        self.assertEqual(gate["tokens"], 200_000)
        self.assertEqual(gate["engine"], "claude")


if __name__ == "__main__":
    unittest.main()
