"""Answering GitHub-backed owner waits: recheck the live ticket, then `wt answer`."""
import json
import subprocess

from ccc_server.decision_github_answers import answer_github_wait
from ccc_server.decision_requests import github_requests


def _ticket(**fields):
    item = dict(id='github:7', ref='OPS-7', title='Repair', status='in_progress', needs_input=True,
                block_kind='input', block_question='[Q9:repair] Approve the plan?',
                blocked_at='2026-01-01T00:00:01Z')
    item.update(fields)
    return item


class Wt:
    """Records argv; `find` returns ``live``, `answer` exits with ``code``."""
    def __init__(self, live=None, code=0):
        self.live, self.code, self.calls = live if live is not None else _ticket(), code, []

    def __call__(self, argv, **_):
        self.calls.append(argv[1:])
        if argv[1] == 'find':
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.live), '')
        return subprocess.CompletedProcess(argv, self.code, '', '')


def _body(**fields):
    data = dict(request_id=github_requests([_ticket()])[0]['request_id'], expected_revision=1,
                text='-yes, go ahead', idempotency_key='key-1')
    data.update(fields)
    return json.dumps(data).encode()


def test_answers_a_live_wait_as_the_human_with_a_receipt_the_ui_accepts():
    wt = Wt()
    payload, status = answer_github_wait(_body(), [_ticket()], executable='wt', runner=wt)
    assert status == 200 and payload['state'] == 'accepted'
    assert payload['request_id'] == json.loads(_body())['request_id']
    assert payload['answer']['text'] == '-yes, go ahead' and payload['answer']['expected_revision'] == 1
    assert wt.calls[-1] == ['answer', '--by', 'human', '--', 'OPS-7', '-yes, go ahead']


def test_a_changed_ticket_is_never_answered():
    for live in (_ticket(needs_input=False), _ticket(blocked_at='2026-02-02T00:00:00Z'),
                 _ticket(block_question='[Q9:repair] A different question?'),
                 _ticket(wait={'on': 'dependency', 'block_at': '2026-01-01T00:00:01Z'})):
        wt = Wt(live)
        payload, status = answer_github_wait(_body(), [_ticket()], executable='wt', runner=wt)
        assert (status, payload['error']) == (409, 'conflict')
        assert all(call[0] != 'answer' for call in wt.calls)


def test_other_sources_fall_through_to_the_registry():
    wt = Wt()
    assert answer_github_wait(_body(request_id='other'), [_ticket()], executable='wt', runner=wt) is None
    assert answer_github_wait(b'not json', [_ticket()], executable='wt', runner=wt) is None
    assert wt.calls == []


def test_bad_requests_and_failed_delivery_are_reported():
    for body in (_body(text='  '), _body(expected_revision=2), _body(idempotency_key='')):
        assert answer_github_wait(body, [_ticket()], executable='wt', runner=Wt())[1] == 400
    assert answer_github_wait(_body(), [_ticket()], executable=None, runner=Wt())[1] == 503
    payload, status = answer_github_wait(_body(), [_ticket()], executable='wt', runner=Wt(code=1))
    assert status == 502 and 'do not resend' in payload['error']
