"""WatchTower's opt-in conditional answer contract; never uses legacy answer.

An adapter instance is inert until explicitly called. No scans or models run.
Every send is conditional on the exact frozen source snapshot and uses the
outbox answer UUID as its source idempotency key. Receipts must name that same
ticket, key and canonical payload before acceptance can be reported.
"""
import json
import subprocess


class WatchTowerAnswers:
    conditional_idempotent = True

    def __init__(self, executable, *, runner=None):
        self.executable = executable
        self.runner = runner or subprocess.run

    @staticmethod
    def _request(row):
        source = row['payload']['source']
        snapshot = source.get('source_snapshot')
        if (source.get('adapter') != 'watchtower-block' or not isinstance(snapshot, dict)
                or not snapshot.get('blocked_at') or snapshot.get('wait_kind') != 'owner'
                or 'block_question' not in snapshot or 'wait' not in snapshot):
            raise ValueError('Exact owner question source snapshot required')
        return dict(idempotency_key=row['answer_id'], text=row['payload']['answer']['text'],
                    expected_blocked_at=snapshot['blocked_at'],
                    expected_block_question=snapshot['block_question'],
                    expected_wait_kind=snapshot['wait_kind'], expected_wait=snapshot['wait'],
                    session_id='', by_kind='human', tid=False, engine=None)

    def _call(self, args):
        result = self.runner([self.executable] + args, capture_output=True, text=True, timeout=60)
        try:
            payload = json.loads(result.stdout)
        except (ValueError, TypeError):
            payload = None
        return result.returncode, payload

    def _receipt(self, row, payload):
        if (isinstance(payload, dict) and payload.get('version') == 1
                and payload.get('accepted') is True
                and payload.get('ref') == row['payload']['source']['origin_id']
                and payload.get('idempotency_key') == row['answer_id']
                and payload.get('request') == self._request(row)):
            return dict(status='accepted', answer_id=row['answer_id'],
                        work_completed=False, source_receipt=payload)
        return dict(status='unknown', answer_id=row['answer_id'])

    def submit(self, row):
        request = self._request(row)
        ref = row['payload']['source']['origin_id']
        code, payload = self._call(['answer-conditional', ref, '--request-json',
                                    json.dumps(request, sort_keys=True)])
        if (code == 2 and isinstance(payload, dict)
                and payload.get('error') in ('conflict', 'invalid_request', 'unsupported')):
            return dict(status='rejected', answer_id=row['answer_id'], reason=payload['error'])
        return self._receipt(row, payload) if code == 0 else dict(status='unknown')

    def lookup(self, row):
        # Validate before even looking up; an old projection lacks the fields
        # needed to prove which payload a source receipt actually accepted.
        self._request(row)
        ref = row['payload']['source']['origin_id']
        code, payload = self._call(['answer-receipt', ref, row['answer_id'], '--json'])
        if code == 1 and isinstance(payload, dict) and payload.get('error') == 'not_found':
            return dict(status='not_found', authoritative=True, answer_id=row['answer_id'])
        return self._receipt(row, payload) if code == 0 else dict(status='unknown')
