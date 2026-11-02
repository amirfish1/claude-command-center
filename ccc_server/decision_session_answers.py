"""Conditional delivery to the explicit session-question source registry.

Integration contract (no HTTP wiring or engine injection here):
* Parent enables AnswerOutbox.record only for explicitly promoted global
  session-question/owner/question records. Freeze the registry content in
  row['payload']['source']; preserve request_id, expected_revision, text, actor,
  answer_id (canonical UUID), and idempotency_key. Never rebuild that snapshot
  on retry. The source registry and outbox must share this registry database.
  Source acceptance changes status to answered and increments revision; the
  outbox must not interpret that accepted source transition as stale rejection.
  Pending recorded/sending/unknown are provisional: local lifecycle changes
  may win until source acceptance. Reconcile a lost response via lookup.
  Parent must guard outbox _claim/_finish registry delivery_state writes: a
  rejected stale send must not overwrite a winning local resolution (local)
  or a different accepted source receipt. Those generic writes are outside
  this adapter; the source receipt and audit remain authoritative.
* SessionAnswers(registry, notify=None).submit(row)/lookup(row) are the small
  conditional_idempotent adapter surface. Acceptance is an atomic source CAS,
  durable source inbox item, and immutable receipt, NOT engine input or work
  completion. Replaying any identity with different bytes is a conflict.
* Existing session_payload GET exposes remote_answers with stable answer IDs.
  The owning session must read the answer, then explicitly call consume via
  SessionQuestions.apply with operation='consume' or consume_payload
  (parent supplies trusted session routing/authentication). The existing
  event_payload facade returns consumption=ack for consume events.
  The receipt's session_consumed=False is frozen at acceptance time; consult
  the separate consumption object for current acknowledgment state.
  Reading/polling never consumes. A consume event acknowledges source/session
  receipt only; it does not assert work completion.
* Notification journal is created in the source acceptance transaction.
  Constructor notify callback receives only {session_id, request_id, answer_id}
  after a durable pending -> uncertain CAS. A callback is attempted at most once;
  uncertain/notified/suppressed are never retried or rerouted. The submit
  replay path never calls transport, including when its journal is pending.
  Explicit notify can claim a pending journal (safe recovery after commit but
  before claim); lookup/poll never wake. A crash after claim is uncertain and
  remains recoverable through inbox polling and explicit consume, without
  automatic resend. There is no claim lease or timeout that enables resend.
  Notification state is durable and exposed via notification_state()/poll's
  notification object; last_notification is only a convenience snapshot.
  The accepted receipt contains frozen routing/notification-at-acceptance
  metadata, consumed=False and session_consumed=False. Current consumption
  and notification are separate objects. Transport success is never session
  receipt. Parent may use an exact-original-session advisory transport with
  no queue/fallback and IDs-only polling instruction, never raw answer text.

Constructors are inert. lookup/poll/notification_state use mode=ro snapshots
and never initialize or migrate. Missing old journals have virtual uncertain
read projections; only submit/notify/consume initialize durable schema.
This module enrolls nothing, parses no transcripts, and starts no workers.
"""
import json
import sqlite3
from contextlib import contextmanager
import uuid
from datetime import datetime, timezone

from ccc_server.decision_registry import _json
from ccc_server.decision_sessions import SessionQuestionConflict, SessionQuestions, _text, _uuid


class SessionAnswerAdapter:
    conditional_idempotent = True

    def __init__(self, registry):
        self.registry = registry

    def _initialize(self):
        """Schema/migration are allowed only on explicit write operations."""
        registry = self.registry
        SessionQuestions(registry)
        with registry._transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS session_answer_receipts (
                answer_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                request_id TEXT NOT NULL, session_id TEXT NOT NULL,
                payload TEXT NOT NULL, receipt TEXT NOT NULL,
                consumed TEXT
            )''')
            db.execute('''CREATE TABLE IF NOT EXISTS session_answer_notifications (
                answer_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                payload TEXT NOT NULL, state TEXT NOT NULL,
                attempt TEXT, result TEXT NOT NULL
            )''')
            # Older receipts predate journaling; their wake outcome cannot be
            # proved. Never implicitly resend a potentially delivered wake.
            for row in db.execute('SELECT * FROM session_answer_receipts').fetchall():
                receipt = json.loads(row['receipt'])
                if receipt['status'] == 'accepted':
                    self._new_notification(db, receipt, state='uncertain', consumed=row['consumed'] is not None)
            for old in db.execute("SELECT * FROM session_answer_notifications "
                                  "WHERE state IN ('claimed','unknown')").fetchall():
                result = json.loads(old['result'])
                result.update(journal_state='uncertain', notification='uncertain')
                db.execute("UPDATE session_answer_notifications SET state='uncertain',result=? "
                           "WHERE answer_id=?", (_json(result), old['answer_id']))

    @contextmanager
    def _read(self):
        """Open an actual SQLite read-only snapshot, without registry setup."""
        if not self.registry.db_path.exists():
            yield None
            return
        db = sqlite3.connect(self.registry.db_path.resolve().as_uri() + '?mode=ro',
                             uri=True, timeout=self.registry.timeout, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            yield db
        finally:
            db.close()

    @staticmethod
    def _has_table(db, name):
        return db is not None and db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    @staticmethod
    def _notification_default(receipt, *, state='uncertain', consumed=False):
        ids = {key: receipt[key] for key in ('session_id', 'request_id', 'answer_id')}
        return dict(ids, durable_acceptance=True, notification=state,
                    journal_state=state, attempt_id=None, session_consumed=consumed,
                    consumed=consumed, work_completed=False)

    def _read_notification(self, db, receipt, consumed):
        row = db.execute('SELECT * FROM session_answer_notifications WHERE answer_id=?',
                         (receipt['answer_id'],)).fetchone() if self._has_table(
                             db, 'session_answer_notifications') else None
        if row is None:
            return dict(self._notification_default(receipt, consumed=consumed), virtual=True)
        result = json.loads(row['result'])
        if row['state'] in ('claimed', 'unknown'):
            result.update(notification='uncertain', journal_state='uncertain', virtual=True)
        # Read projection only: acknowledgment never rewrites transport outcome.
        result.update(consumed=consumed, session_consumed=consumed)
        return result

    @staticmethod
    def _binding(row):
        if not isinstance(row, dict):
            raise ValueError('Answer row required')
        _uuid(row.get('answer_id'), 'answer_id')
        _uuid(row.get('request_id'), 'request_id')
        _text(row.get('idempotency_key'), 'idempotency_key', 200)
        payload = row['payload']
        answer, source = payload['answer'], payload['source']
        if answer.get('request_id') != row['request_id']:
            raise ValueError('Answer request identity mismatch')
        if type(answer.get('expected_revision')) is not int or answer['expected_revision'] < 1:
            raise ValueError('Positive expected_revision required')
        _text(answer.get('text'), 'text', 24000)
        _text(answer.get('actor'), 'actor', 200)
        for field in ('session_id', 'question_id', 'generation'):
            _uuid(source.get(field), field)
        _text(source.get('body'), 'body', 24000)
        return _json(dict(answer_id=row['answer_id'], request_id=row['request_id'],
                          idempotency_key=row['idempotency_key'], payload=payload))

    @staticmethod
    def _existing(db, row, encoded):
        old = db.execute('SELECT * FROM session_answer_receipts WHERE answer_id=? OR idempotency_key=?',
                         (row['answer_id'], row['idempotency_key'])).fetchone()
        if old and old['payload'] != encoded:
            raise SessionQuestionConflict('Answer UUID or idempotency key reused with another payload')
        return json.loads(old['receipt']) if old else None

    def lookup(self, row):
        """Authoritative, frozen receipt lookup; never sends or consumes input."""
        encoded = self._binding(row)
        with self._read() as db:
            old = self._existing(db, row, encoded) if self._has_table(db, 'session_answer_receipts') else None
            return old or dict(
                status='not_found', answer_id=row['answer_id'], authoritative=True)

    def submit(self, row):
        """Commit durable acceptance without transport; replay is inert."""
        return self._submit(row)[0]

    def _submit(self, row):
        """Return (frozen_receipt, newly_recorded) from the source transaction."""
        encoded = self._binding(row)
        self._initialize()
        answer, source = row['payload']['answer'], row['payload']['source']
        with self.registry._transaction() as db:
            old = self._existing(db, row, encoded)
            if old:
                return old, False
            current = db.execute('SELECT * FROM requests WHERE request_id=?',
                                 (row['request_id'],)).fetchone()
            reason = 'request_changed_or_not_promoted'
            eligible = bool(current and current['adapter'] == 'session-question'
                and current['origin_id'] == source['session_id'] + ':' + source['question_id']
                and current['generation'] == source['generation']
                and current['revision'] == answer['expected_revision']
                and current['status'] == 'open'
                and json.loads(current['content']) == source
                and source.get('adapter') == 'session-question'
                and source.get('visibility') == 'global'
                and source.get('kind') == 'question' and source.get('wait_kind') == 'owner'
                and db.execute("SELECT 1 FROM session_question_events WHERE request_id=? "
                               "AND operation='promote'", (row['request_id'],)).fetchone())
            if eligible and current['answer_state'] != 'none':
                # Outbox.record sets recorded before source delivery. Accept
                # only the exact durable outbox owner, never another answer.
                table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='answers'").fetchone()
                pending = db.execute('SELECT * FROM answers WHERE answer_id=?',
                                     (row['answer_id'],)).fetchone() if table else None
                eligible = bool(pending and pending['request_id'] == row['request_id']
                    and pending['idempotency_key'] == row['idempotency_key']
                    and json.loads(pending['payload']) == row['payload']
                    and pending['state'] in ('pending', 'sending', 'unknown')
                    and current['answer_state'] == 'recorded'
                    and current['delivery_state'] in ('pending', 'sending', 'unknown'))
            now = datetime.now(timezone.utc).isoformat()
            receipt = dict(status='accepted' if eligible else 'rejected', answer_id=row['answer_id'],
                           request_id=row['request_id'], session_id=source['session_id'],
                           question_id=source['question_id'], generation=source['generation'],
                           source_revision=answer['expected_revision'], recorded_at=now,
                           acceptance='source_inbox' if eligible else None,
                           session_consumed=False, consumed=False, work_completed=False)
            if eligible:
                receipt['routing'] = dict(state='source_inbox', session_id=source['session_id'],
                                          request_id=row['request_id'], answer_id=row['answer_id'])
                receipt['notification'] = dict(state='pending', attempt_id=None)
            if eligible:
                db.execute("UPDATE requests SET status='answered', answer_state='recorded', "
                           "delivery_state='accepted', revision=revision+1 WHERE request_id=?",
                           (row['request_id'],))
                self._audit(db, str(uuid.uuid5(uuid.UUID(row['answer_id']), 'accepted')),
                            row['request_id'], 'answer_remote_accepted',
                            dict(answer_id=row['answer_id'], source=source, answer=answer), now, receipt)
            else:
                receipt['reason'] = reason
            db.execute('INSERT INTO session_answer_receipts VALUES (?,?,?,?,?,?,NULL)',
                       (row['answer_id'], row['idempotency_key'], row['request_id'],
                        source['session_id'], encoded, _json(receipt)))
            if eligible:
                self._new_notification(db, receipt)
            return receipt, True

    @staticmethod
    def _audit(db, event_id, request_id, operation, payload, now, result):
        db.execute('INSERT INTO session_question_events VALUES (?,?,?,?,?,?)',
                   (event_id, request_id, operation, _json(payload), now, _json(result)))

    def poll(self, session_id):
        """Return all accepted answers for this session, including acknowledged ones."""
        _uuid(session_id, 'session_id')
        with self._read() as db:
            result = []
            if not self._has_table(db, 'session_answer_receipts'):
                return result
            for row in db.execute('SELECT * FROM session_answer_receipts WHERE session_id=? ORDER BY rowid',
                                  (session_id,)):
                receipt = json.loads(row['receipt'])
                if receipt['status'] != 'accepted':
                    continue
                binding = json.loads(row['payload'])
                result.append(dict(receipt=receipt, answer=binding['payload']['answer'],
                                   source=binding['payload']['source'],
                                   consumption=json.loads(row['consumed']) if row['consumed'] else None,
                                   notification=self._read_notification(
                                       db, receipt, row['consumed'] is not None)))
            return result

    def consume(self, event):
        """Explicit session acknowledgment; idempotent per event and per answer.

        Required exact keys: event_id, session_id, answer_id, request_id,
        question_id, generation, source_message_id; optional operation='consume'
        is accepted for SessionQuestions events. Session identity is a
        binding, not authentication; the HTTP caller must authorize it.
        """
        fields = {'event_id', 'session_id', 'answer_id', 'request_id', 'question_id',
                  'generation', 'source_message_id'}
        if isinstance(event, dict) and event.get('operation') == 'consume':
            fields.add('operation')
        if not isinstance(event, dict) or set(event) != fields:
            raise ValueError('Exact consumption fields required')
        for field in fields - {'source_message_id', 'operation'}:
            _uuid(event[field], field)
        _text(event['source_message_id'], 'source_message_id', 200)
        self._initialize()
        with self.registry._transaction() as db:
            old = db.execute('SELECT payload,result FROM session_question_events WHERE event_id=?',
                             (event['event_id'],)).fetchone()
            if old:
                if old['payload'] != _json(event):
                    raise SessionQuestionConflict('Event ID reused with another payload')
                return json.loads(old['result'])
            row = db.execute('SELECT * FROM session_answer_receipts WHERE answer_id=?',
                             (event['answer_id'],)).fetchone()
            receipt = json.loads(row['receipt']) if row else {}
            if receipt.get('status') != 'accepted' or any(
                    event[key] != receipt.get(key) for key in
                    ('session_id', 'request_id', 'question_id', 'generation')):
                raise SessionQuestionConflict('Accepted answer does not belong to this question/session')
            now = datetime.now(timezone.utc).isoformat()
            result = json.loads(row['consumed']) if row['consumed'] else dict(
                answer_id=event['answer_id'], session_id=event['session_id'],
                source_message_id=event['source_message_id'], consumed_at=now,
                session_consumed=True, consumed=True, work_completed=False)
            db.execute('UPDATE session_answer_receipts SET consumed=? WHERE answer_id=?',
                       (_json(result), event['answer_id']))
            notification = db.execute(
                'SELECT result FROM session_answer_notifications WHERE answer_id=?',
                (event['answer_id'],)).fetchone()
            if notification:
                transport = json.loads(notification[0])
                transport.update(session_consumed=True, consumed=True)
                db.execute('UPDATE session_answer_notifications SET state=?,result=? WHERE answer_id=?',
                           (transport['journal_state'], _json(transport), event['answer_id']))
            self._audit(db, event['event_id'], event['request_id'], 'answer_remote_consumed', event, now, result)
            return result

    @staticmethod
    def _new_notification(db, receipt, *, state='pending', consumed=False):
        ids = {key: receipt[key] for key in ('session_id', 'request_id', 'answer_id')}
        if consumed:
            state = 'suppressed'
        result = dict(ids, durable_acceptance=True, notification=state,
                      journal_state=state, attempt_id=None, session_consumed=consumed,
                      consumed=consumed, work_completed=False)
        db.execute('INSERT OR IGNORE INTO session_answer_notifications VALUES (?,?,?,?,NULL,?)',
                   (receipt['answer_id'], receipt['session_id'], _json(ids), state, _json(result)))

    def notification_state(self, answer_id, session_id):
        """Read-only current wake state; missing old journals are virtually uncertain."""
        _uuid(answer_id, 'answer_id')
        _uuid(session_id, 'session_id')
        with self._read() as db:
            row = db.execute('SELECT * FROM session_answer_receipts WHERE answer_id=? AND session_id=?',
                             (answer_id, session_id)).fetchone() if self._has_table(
                                 db, 'session_answer_receipts') else None
            receipt = json.loads(row['receipt']) if row else {}
            if receipt.get('status') != 'accepted':
                raise SessionQuestionConflict('No accepted answer for this session')
            return self._read_notification(db, receipt, row['consumed'] is not None)

    def _claim_notification(self, answer_id, session_id):
        """Persist ownership before any external side effect; never expires."""
        _uuid(answer_id, 'answer_id')
        _uuid(session_id, 'session_id')
        self._initialize()
        with self.registry._transaction() as db:
            row = db.execute('SELECT * FROM session_answer_notifications WHERE answer_id=? AND session_id=?',
                             (answer_id, session_id)).fetchone()
            if row is None:
                raise SessionQuestionConflict('No accepted answer for this session')
            result = json.loads(row['result'])
            if row['state'] != 'pending':
                return None, result
            consumed = db.execute('SELECT consumed FROM session_answer_receipts WHERE answer_id=?',
                                  (answer_id,)).fetchone()[0] is not None
            attempt = str(uuid.uuid4())
            result.update(notification='suppressed' if consumed else 'uncertain',
                          journal_state='suppressed' if consumed else 'uncertain',
                          attempt_id=attempt, session_consumed=consumed, consumed=consumed)
            db.execute('UPDATE session_answer_notifications SET state=?,attempt=?,result=? WHERE answer_id=?',
                       (result['journal_state'], attempt, _json(result), answer_id))
            self._audit(db, attempt, result['request_id'], 'answer_remote_notify_claimed',
                        json.loads(row['payload']), datetime.now(timezone.utc).isoformat(), result)
            return None if consumed else json.loads(row['payload']), result

    def notify(self, answer_id, session_id, wake):
        """Explicitly claim a pending wake once; replay/uncertainty never sends."""
        if not callable(wake):
            raise ValueError('wake must be callable')
        ids, result = self._claim_notification(answer_id, session_id)
        if ids is None:
            return result
        try:
            state = 'notified' if wake(ids) is True else 'uncertain'
        except Exception:
            state = 'uncertain'
        result.update(notification=state, journal_state=state)
        with self.registry._transaction() as db:
            consumed = db.execute('SELECT consumed FROM session_answer_receipts WHERE answer_id=?',
                                  (answer_id,)).fetchone()[0] is not None
            result.update(session_consumed=consumed, consumed=consumed)
            changed = db.execute("UPDATE session_answer_notifications SET state=?,result=? "
                                 "WHERE answer_id=? AND state='uncertain' AND attempt=?",
                                 (state, _json(result), answer_id, result['attempt_id']))
            if changed.rowcount:
                self._audit(db, str(uuid.uuid5(uuid.UUID(result['attempt_id']), 'finished')),
                            result['request_id'], 'answer_remote_notify_finished', ids,
                            datetime.now(timezone.utc).isoformat(), result)
        return result


class SessionAnswers(SessionAnswerAdapter):
    """Preferred outbox adapter with an optional advisory safe wake callback.

    The callback receives only session_id/request_id/answer_id. It runs once
    after durable claim, only for newly accepted submit calls. Replays do not
    wake even when pending; explicit notify can recover pending journals.
    Uncertain or completed notifications are never resent. Inspect
    notification_state/poll for the durable journal, consume for session receipt.
    """

    def __init__(self, registry, notify=None):
        if notify is not None and not callable(notify):
            raise ValueError('notify must be a callable or None')
        self._wake = notify
        self.last_notification = None
        super().__init__(registry)

    def submit(self, row):
        receipt, newly_recorded = self._submit(row)
        if receipt['status'] == 'accepted':
            try:
                if newly_recorded and self._wake is not None:
                    self.last_notification = self.notify(
                        receipt['answer_id'], receipt['session_id'], self._wake)
                else:
                    self.last_notification = self.notification_state(
                        receipt['answer_id'], receipt['session_id'])
            except Exception:
                # Wake/poll infrastructure can fail after durable acceptance.
                # Never turn that advisory failure into a source rejection.
                self.last_notification = dict(answer_id=receipt['answer_id'],
                    durable_acceptance=True, notification='uncertain', work_completed=False)
        return receipt


def consume_payload(payload, *, state_root=None):
    """HTTP-independent POST helper; parent must authenticate the owning session."""
    import sqlite3
    from pathlib import Path
    from ccc_server import decision_inbox
    from ccc_server.decision_registry import DecisionRegistry
    try:
        root = Path(state_root) if state_root else decision_inbox._state_root()
        registry = DecisionRegistry(root / 'decision-inbox' / 'requests.sqlite3', timeout=1)
        result = SessionAnswerAdapter(registry).consume(payload)
        return dict(ok=True, consumption=result, work_completed=False), 200
    except SessionQuestionConflict as error:
        return dict(ok=False, error=str(error)), 409
    except (ValueError, TypeError) as error:
        return dict(ok=False, error=str(error)), 400
    except (OSError, sqlite3.Error):
        return dict(ok=False, error='Session answer registry is unavailable'), 503
