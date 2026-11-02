"""Durable answers, separate from source acceptance and completed work.

This module exposes no HTTP handler and starts no worker. Callers must supply
a conditional, idempotent source adapter. Interrupted deliveries remain unknown
until a read-only source receipt lookup resolves them; they are never blindly
retried. Legacy actions and transcript questions are deliberately unsupported.
"""
import json
import uuid
from datetime import datetime, timezone


class AnswerConflict(ValueError):
    """The request changed or this key already describes another answer."""


class AnswerOutbox:
    def __init__(self, registry):
        self.registry = registry
        with registry._transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS answers (
                answer_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
                state TEXT NOT NULL, attempt TEXT, receipt TEXT
            )''')
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_answer ON answers(request_id) "
                       "WHERE state != 'rejected'")

    @staticmethod
    def _row(row):
        if row is None:
            return None
        result = dict(row)
        result['payload'] = json.loads(result['payload'])
        result['receipt'] = json.loads(result['receipt']) if result['receipt'] else None
        return result

    def get(self, answer_id):
        with self.registry._transaction() as db:
            return self._row(db.execute('SELECT * FROM answers WHERE answer_id=?',
                                       (answer_id,)).fetchone())

    def record(self, request_id, *, expected_revision, text, idempotency_key, actor='owner'):
        """Atomically save one answer and pending delivery; never contact a source."""
        if (not isinstance(text, str) or not text.strip() or len(text) > 24000
                or not isinstance(idempotency_key, str) or not idempotency_key.strip()
                or len(idempotency_key) > 200 or type(expected_revision) is not int
                or not isinstance(actor, str) or not actor):
            raise ValueError('A nonempty answer/key/actor and integer revision are required')
        supplied = dict(request_id=request_id, expected_revision=expected_revision,
                        text=text, actor=actor)
        with self.registry._transaction() as db:
            old = db.execute('SELECT * FROM answers WHERE idempotency_key=?',
                             (idempotency_key,)).fetchone()
            if old:
                record = self._row(old)
                if record['payload']['answer'] != supplied:
                    raise AnswerConflict('Idempotency key already used with another payload')
                return record
            request = db.execute('SELECT * FROM requests WHERE request_id=?',
                                 (request_id,)).fetchone()
            if request is None:
                raise AnswerConflict('Request not found')
            content = json.loads(request['content'])
            snapshot = content.get('source_snapshot')
            if (request['adapter'] != 'watchtower-block'
                    or content.get('visibility') != 'global'
                    or content.get('kind') != 'question'
                    or content.get('wait_kind') != 'owner'
                    or not isinstance(snapshot, dict)
                    or snapshot.get('blocked_at') != request['generation']
                    or not isinstance(snapshot.get('block_question'), str)
                    or not snapshot['block_question'].strip()
                    or snapshot.get('wait_kind') != 'owner'
                    or 'wait' not in snapshot
                    or (snapshot['wait'] is not None and not isinstance(snapshot['wait'], dict))):
                raise AnswerConflict('Request has no supported source delivery contract')
            binding = db.execute("SELECT value FROM metadata WHERE key='source_scope:watchtower-block'").fetchone()
            if (not binding or json.loads(binding[0])['scope']['store_id'] != snapshot.get('store_id')):
                raise AnswerConflict('Request is not bound to the authoritative source store')
            if (request['revision'] != expected_revision or request['status'] != 'open'
                    or (request['answer_state'] != 'none' and request['delivery_state'] != 'rejected')):
                raise AnswerConflict('Request changed or already has an answer')
            key = 'delivery_owner:' + request_id
            db.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)', (key, 'ccc-outbox'))
            if db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()[0] != 'ccc-outbox':
                raise AnswerConflict('This request has another answer delivery owner')
            answer_id = str(uuid.uuid4())
            payload = json.dumps({'answer': supplied, 'source': content,
                                  'created_at': datetime.now(timezone.utc).isoformat()}, sort_keys=True)
            db.execute('INSERT INTO answers VALUES (?, ?, ?, ?, ?, NULL, NULL)',
                       (answer_id, request_id, idempotency_key, payload, 'pending'))
            db.execute("UPDATE requests SET answer_state='recorded', delivery_state='pending' "
                       'WHERE request_id=?', (request_id,))
            return self._row(db.execute('SELECT * FROM answers WHERE answer_id=?',
                                       (answer_id,)).fetchone())

    def _claim(self, answer_id):
        with self.registry._transaction() as db:
            pending = db.execute("SELECT * FROM answers WHERE answer_id=? AND state='pending'",
                                 (answer_id,)).fetchone()
            if not pending:
                return None
            request = db.execute('SELECT status,revision FROM requests WHERE request_id=?',
                                 (pending['request_id'],)).fetchone()
            expected = json.loads(pending['payload'])['answer']['expected_revision']
            if not request or request['status'] != 'open' or request['revision'] != expected:
                receipt = json.dumps({'status': 'rejected', 'answer_id': answer_id,
                                      'reason': 'request_changed_before_delivery'})
                db.execute("UPDATE answers SET state='rejected', receipt=? WHERE answer_id=?",
                           (receipt, answer_id))
                db.execute("UPDATE requests SET delivery_state='rejected' WHERE request_id=?",
                           (pending['request_id'],))
                return None
            token = str(uuid.uuid4())
            changed = db.execute("UPDATE answers SET state='sending', attempt=? "
                                 "WHERE answer_id=? AND state='pending'", (token, answer_id))
            if not changed.rowcount:
                return None
            db.execute("UPDATE requests SET delivery_state='sending' WHERE request_id="
                       '(SELECT request_id FROM answers WHERE answer_id=?)', (answer_id,))
            return self._row(db.execute('SELECT * FROM answers WHERE answer_id=?',
                                       (answer_id,)).fetchone())

    def _finish(self, answer_id, attempt, state, receipt):
        with self.registry._transaction() as db:
            changed = db.execute('UPDATE answers SET state=?, receipt=? WHERE answer_id=? '
                                 "AND attempt=? AND state IN ('sending','unknown')",
                                 (state, json.dumps(receipt, sort_keys=True), answer_id, attempt))
            if changed.rowcount:
                db.execute('UPDATE requests SET delivery_state=? WHERE request_id='
                           '(SELECT request_id FROM answers WHERE answer_id=?)',
                           (state, answer_id))
        return self.get(answer_id)

    def dispatch(self, answer_id, adapter):
        """Only adapters explicitly promising source CAS and idempotency may run."""
        if not getattr(adapter, 'conditional_idempotent', False):
            raise ValueError('Conditional idempotent source adapter required')
        row = self._claim(answer_id)
        if not row:
            return self.get(answer_id)
        try:
            receipt = adapter.submit(row)
            if (not isinstance(receipt, dict) or receipt.get('status') not in ('accepted', 'rejected')
                    or receipt.get('answer_id') != answer_id):
                receipt = {'status': 'unknown'}
        except Exception:
            # The source may have accepted the answer before the response was
            # lost. No exception establishes that it is safe to send again.
            receipt = {'status': 'unknown'}
        return self._finish(answer_id, row['attempt'], receipt['status'], receipt)

    def reconcile(self, answer_id, adapter):
        """Resolve an interrupted send using a receipt lookup, without resending."""
        if not getattr(adapter, 'conditional_idempotent', False):
            raise ValueError('Conditional idempotent source adapter required')
        row = self.get(answer_id)
        if not row or row['state'] not in ('sending', 'unknown'):
            return row
        try:
            receipt = adapter.lookup(row)
        except Exception:
            return row
        if (isinstance(receipt, dict) and receipt.get('status') in ('accepted', 'rejected')
                and receipt.get('answer_id') == answer_id):
            return self._finish(answer_id, row['attempt'], receipt['status'], receipt)
        if (isinstance(receipt, dict) and receipt.get('status') == 'not_found'
                and receipt.get('authoritative') is True
                and getattr(adapter, 'conditional_idempotent', False)):
            return self._finish(answer_id, row['attempt'], 'pending', receipt)
        return row
