import types
import unittest

from ccc_server import auto_decisions as ad


def item(hist):
    return {'ref': 'CCC-1', 'title': 't', 'history': hist}


class AutoDecisionTests(unittest.TestCase):
    def test_reversed_first_and_revertable(self):
        a = {'event': 'auto_decision', 'decision_id': 'a', 'at': '2026-01-02', 'class': 'code-change-revertable', 'commit': 'abc'}
        b = {'event': 'auto_decision', 'decision_id': 'b', 'at': '2026-01-03', 'class': 'code-change-revertable', 'commit': 'def'}
        r = {'event': 'auto_decision_reversed', 'decision_id': 'a', 'at': '2026-01-04'}
        out = ad.auto_decision_rows([item([a, b, r])])
        self.assertEqual([d['decision_id'] for d in out['decisions']], ['a', 'b'])
        self.assertTrue(out['decisions'][0]['reversed'])
        self.assertFalse(out['decisions'][0]['revertable'])
        self.assertTrue(out['decisions'][1]['revertable'])

    def test_failed_followthrough_sorts_first(self):
        try:
            import watchtower.autodecide  # noqa: F401
        except ImportError:
            self.skipTest('watchtower not installed')
        ok = {'event': 'auto_decision', 'decision_id': 'ok', 'at': '2026-01-09'}
        bad = {'event': 'auto_decision', 'decision_id': 'bad', 'at': '2026-01-01'}
        close = {'event': 'close', 'resolution': {'summary': 'PARTIAL: half done'}}
        out = ad.auto_decision_rows([item([ok]), item([bad, close])])
        self.assertEqual([d['decision_id'] for d in out['decisions']], ['bad', 'ok'])
        self.assertEqual(out['decisions'][0]['followthrough']['state'], 'failed')
        self.assertEqual(out['decisions'][1]['followthrough']['state'], 'pending')

    def test_contradiction(self):
        out = ad.auto_decision_rows([item([{'event': 'contradiction', 'at': 'x', 'text': 'no'}])])
        self.assertEqual(out['contradictions'][0]['ref'], 'CCC-1')

    def test_revert_validates_ref_and_reports_failure(self):
        self.assertEqual(ad.revert('rm -rf')[1], 400)
        fail = lambda *a, **k: types.SimpleNamespace(returncode=1, stdout='', stderr='nope')
        self.assertEqual(ad.revert('CCC-1', runner=fail)[1], 409)
        ok = lambda *a, **k: types.SimpleNamespace(returncode=0, stdout='REVERTED', stderr='')
        self.assertEqual(ad.revert('CCC-1', runner=ok)[0]['message'], 'REVERTED')


if __name__ == '__main__':
    unittest.main()
