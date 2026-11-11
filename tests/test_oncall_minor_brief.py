"""An enabled minor policy must include enough instructions to use its gate."""
from ccc_server.oncall import customers


class Recorder:
    def add(self, queue, **kwargs):
        self.text = kwargs['text']
        return 'TEST-1'

    def report_update(self, *args, **kwargs):
        pass

    def log(self, *args, **kwargs):
        pass


def test_minor_brief_requires_verified_commit_and_keeps_hard_stops():
    recorder = Recorder()
    report = {'id': 1, 'source': 'test', 'fingerprint': 'one', 'text': 'The label has spaces.'}
    customers.file_report({'queue': 'TEST', 'minor_decisions': {'enabled': True}},
                          recorder, recorder, report)
    assert '--commit <verified full commit SHA>' in recorder.text
    assert "--undo 'git revert <same SHA>'" in recorder.text
    assert "--attest 'money=no,customer-sends=no,public-posts=no,ad-spend=no,irreversible=no'" in recorder.text
    assert 'This single comma-separated argument also works on older WatchTower versions.' in recorder.text
    assert 'before recording the decision' in recorder.text
    assert 'Ask the owner before those actions' in recorder.text
    assert 'only to genuine discretionary small choices' in recorder.text
    assert ('not an extra approval gate for a specified bug fix or an action '
            'the owner already answered') in recorder.text
    assert 'truthful numeric --confidence in 0..1' in recorder.text
    assert recorder.text.index('----- end report -----') < recorder.text.index('Small reversible')


def test_disabled_policy_does_not_suggest_automatic_decisions():
    recorder = Recorder()
    customers.file_report({'queue': 'TEST'}, recorder, recorder,
                          {'id': 1, 'source': 'test', 'fingerprint': 'one'})
    assert 'Small reversible' not in recorder.text
