"""Validation and cache checks; no external CLI mocks."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from ccc_server import blocked_reading as reading


class BlockedReadingTests(unittest.TestCase):
    def test_rejects_invalid_generated_shape(self):
        for data in ([], {}, {'question': 'Hi'}, {'question': 4}):
            with self.assertRaises(ValueError):
                reading.validate(data)

    def test_exact_source_cache_works_without_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            source = 'Waiting on another task.'
            value = dict(question='Work is waiting', proposal='Finish the other task.',
                         consequence='No choice is requested.', context='A dependency is unfinished.', alternatives=[])
            key = hashlib.sha256((reading._PROMPT + source).encode()).hexdigest()
            Path(directory, key + '.json').write_text(json.dumps(value))
            result = reading.explain(source, claude_bin=None, cache_dir=directory, scratch_dir=directory)
            self.assertTrue(result['cached'])
            self.assertEqual(value, result['reading'])
            changed = reading.explain(source + ' Changed.', claude_bin=None, cache_dir=directory, scratch_dir=directory)
            self.assertFalse(changed['ok'])

    def test_invalid_input_never_starts_cli(self):
        for source in (None, [], '', 'x' * 16001):
            result = reading.explain(source, claude_bin=None, cache_dir='', scratch_dir='')
            self.assertFalse(result['ok'])


if __name__ == '__main__':
    unittest.main()
