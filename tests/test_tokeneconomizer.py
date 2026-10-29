import json
from pathlib import Path
import tempfile
import unittest

from ccc_server.tokeneconomizer import session_annotation


class TokenEconomizerAnnotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, relative, value):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value))

    def record(self):
        self.write('sessions/codex/test-session.json', {'key': 'test-repo'})
        self.write('repos/test-repo/codex/sessions/test-session.json',
                   {'session_id': 'test-session'})
        return self.root / 'repos/test-repo/codex'

    def test_observation_does_not_imply_optimization(self):
        self.assertEqual(session_annotation('test-session', self.root),
                         {'observed': False, 'actions': []})
        self.record()
        self.assertEqual(session_annotation('test-session', self.root),
                         {'observed': True, 'actions': []})

    def test_only_this_sessions_delivered_actions_are_shown(self):
        base = self.record()
        events = [
            {'session_id': 'other-session', 'kind': 'repomap', 'shadow': False},
            {'session_id': 'test-session', 'kind': 'nudge', 'shadow': True},
            {'session_id': 'test-session', 'kind': 'inject', 'shadow': False},
            {'session_id': 'test-session', 'kind': 'abort', 'shadow': False},
        ]
        (base / 'events.jsonl').write_text('\n'.join(map(json.dumps, events)) + '\n{partial')
        (base / 'ledger.jsonl').write_text(json.dumps({
            'session_id': 'test-session', 'shadow': False, 'untool_applied': ['goals']}))
        self.assertEqual(session_annotation('test-session', self.root)['actions'],
                         ['learned facts injected', 'still-stuck warning', 'tools pruned'])

    def test_untrusted_paths_and_malformed_state_are_ignored(self):
        self.assertFalse(session_annotation('../outside', self.root)['observed'])
        self.write('sessions/codex/test-session.json', {'key': '../outside'})
        self.assertFalse(session_annotation('test-session', self.root)['observed'])
        self.write('sessions/codex/test-session.json', {'key': ['invalid']})
        self.assertFalse(session_annotation('test-session', self.root)['observed'])

    def test_large_log_tail_and_live_injected_facts(self):
        base = self.record()
        self.write('repos/test-repo/codex/sessions/test-session.json',
                   {'session_id': 'test-session', 'injected_chars': 100})
        event = {'session_id': 'test-session', 'kind': 'nudge', 'shadow': False}
        (base / 'events.jsonl').write_text('x' * 300000 + '\n' + json.dumps(event) + '\n')
        self.assertEqual(session_annotation('test-session', self.root)['actions'],
                         ['learned facts injected', 'loop redirected'])

    def test_symlink_outside_state_and_symlink_loop_are_ignored(self):
        base = self.root / 'sessions/codex'
        base.mkdir(parents=True)
        (base / 'test-session.json').symlink_to(self.root.parent / 'outside.json')
        self.assertFalse(session_annotation('test-session', self.root)['observed'])
        (base / 'test-session.json').unlink()
        (base / 'test-session.json').symlink_to('test-session.json')
        self.assertFalse(session_annotation('test-session', self.root)['observed'])


if __name__ == '__main__':
    unittest.main()
