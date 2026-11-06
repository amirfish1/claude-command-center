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
                or not snapshot.get('blocked_at') or not snapshot.get('store_id')
                or snapshot.get('wait_kind') != 'owner'
                or 'block_question' not in snapshot or 'wait' not in snapshot):
            raise ValueError('Exact owner question source snapshot required')
        request = dict(idempotency_key=row['answer_id'], text=row['payload']['answer']['text'],
                    expected_blocked_at=snapshot['blocked_at'],
                    expected_block_question=snapshot['block_question'],
                    expected_wait_kind=snapshot['wait_kind'], expected_wait=snapshot['wait'],
                    session_id='', by_kind='human', tid=False, engine=None)
        if snapshot.get('store_id'):
            request['expected_store_id'] = snapshot['store_id']
        return request

    def _call(self, args):
        result = self.runner([self.executable] + args, capture_output=True, text=True, timeout=60)
        try:
            payload = json.loads(result.stdout)
        except (ValueError, TypeError):
            payload = None
        return result.returncode, payload

    def _receipt(self, row, payload):
        expected = self._request(row)
        actual = payload.get('request') if isinstance(payload, dict) else None
        if isinstance(actual, dict):
            actual = dict(actual)
            # Older receipts predate this optional field; omitted and null
            # have identical meaning. A concrete source identity must match.
            actual.setdefault('expected_store_id', None)
        expected.setdefault('expected_store_id', None)
        if (isinstance(payload, dict) and payload.get('version') == 1
                and payload.get('accepted') is True
                and payload.get('ref') == row['payload']['source']['origin_id']
                and payload.get('idempotency_key') == row['answer_id']
                and actual == expected):
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
        request = self._request(row)
        ref = row['payload']['source']['origin_id']
        args = ['answer-receipt', ref, row['answer_id'], '--json']
        if request.get('expected_store_id'):
            args += ['--expected-store-id', request['expected_store_id']]
        code, payload = self._call(args)
        if code == 1 and isinstance(payload, dict) and payload.get('error') == 'not_found':
            return dict(status='not_found', authoritative=True, answer_id=row['answer_id'])
        return self._receipt(row, payload) if code == 0 else dict(status='unknown')


class WatchTowerCloses:
    """Close the exact observed owner question; no worker is woken."""

    def __init__(self, executable, *, runner=None):
        self.executable = executable
        self.runner = runner or subprocess.run

    @staticmethod
    def _request(record, key, reason):
        snapshot = record['content'].get('source_snapshot')
        if (record.get('adapter') != 'watchtower-block' or not isinstance(snapshot, dict)
                or not snapshot.get('blocked_at') or not snapshot.get('store_id')
                or snapshot.get('wait_kind') != 'owner'
                or 'block_question' not in snapshot or 'wait' not in snapshot):
            raise ValueError('Exact owner question source snapshot required')
        return dict(idempotency_key=key, reason=reason, by='owner',
                    expected_blocked_at=snapshot['blocked_at'],
                    expected_block_question=snapshot['block_question'],
                    expected_wait_kind=snapshot['wait_kind'], expected_wait=snapshot['wait'],
                    expected_store_id=snapshot['store_id'])

    def close(self, record, key, reason):
        request = self._request(record, key, reason)
        ref = record['origin_id']
        result = self.runner([self.executable, 'close-conditional', ref, '--request-json',
                              json.dumps(request, sort_keys=True)],
                             capture_output=True, text=True, timeout=60)
        try:
            payload = json.loads(result.stdout)
        except (ValueError, TypeError):
            payload = None
        if result.returncode == 2:
            if isinstance(payload, dict) and payload.get('error') in ('conflict', 'invalid_request', 'unsupported'):
                return dict(status='rejected', reason=payload['error'])
            if payload is None:
                # Argument parsing failed: this wt predates the command.
                return dict(status='rejected', reason='unsupported')
        if (result.returncode == 0 and isinstance(payload, dict) and payload.get('version') == 1
                and payload.get('accepted') is True and payload.get('ref') == ref
                and payload.get('idempotency_key') == key and payload.get('request') == request):
            return dict(status='accepted', source_receipt=payload)
        return dict(status='unknown')
