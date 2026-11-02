"""Explicit session-question events; transcript tags never enroll requests.

Session authors report creation and local resolution here. Promotion is an
explicit revision-checked event. Remote answers use decision_session_answers
for conditional durable source inbox acceptance and explicit session
consumption; neither implies completed work.
"""
import json
import re
import uuid
from datetime import datetime, timezone

from ccc_server.decision_registry import _aliases, _json


class SessionQuestionConflict(ValueError):
    """An event was reused or its request changed."""


def _uuid(value, field):
    try:
        valid = isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ValueError(field + ' must be a canonical UUID')
    return value


def _text(value, field, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(field + ' must be nonempty text within the length limit')
    return value


class SessionQuestions:
    """Atomic lifecycle and immutable audit events in CCC's local registry."""

    def __init__(self, registry):
        self.registry = registry
        with registry._transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS session_question_events (
                event_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
                operation TEXT NOT NULL, payload TEXT NOT NULL,
                recorded_at TEXT NOT NULL, result TEXT NOT NULL
            )''')

    @staticmethod
    def _validate(event):
        if not isinstance(event, dict):
            raise ValueError('An event object is required')
        common = {'event_id', 'operation', 'session_id'}
        fields = {
            'create': {'question_id', 'generation', 'title', 'body', 'aliases'},
            'edit': {'request_id', 'expected_revision', 'title', 'body'},
            'promote': {'request_id', 'expected_revision', 'reason'},
            'answer_local': {'request_id', 'expected_revision', 'text', 'source_message_id'},
            'withdraw': {'request_id', 'expected_revision', 'reason'},
            'consume': {'request_id', 'answer_id', 'question_id', 'generation', 'source_message_id'},
        }
        operation = event.get('operation')
        if operation not in fields or set(event) - common - fields[operation]:
            raise ValueError('Unsupported session question event or fields')
        _uuid(event.get('event_id'), 'event_id')
        _uuid(event.get('session_id'), 'session_id')
        if operation == 'consume':
            for field in ('request_id', 'answer_id', 'question_id', 'generation'):
                _uuid(event.get(field), field)
            _text(event.get('source_message_id'), 'source_message_id', 200)
            return operation
        if operation == 'create':
            _uuid(event.get('question_id'), 'question_id')
            _uuid(event.get('generation'), 'generation')
            _text(event.get('title'), 'title', 500)
            _text(event.get('body'), 'body', 24000)
            aliases = event.get('aliases', [])
            if (not isinstance(aliases, list) or len(aliases) > 20
                    or any(not isinstance(a, str) or not re.fullmatch(r'\[Q\d+:[a-z0-9-]+\]', a)
                           for a in aliases)):
                raise ValueError('aliases must contain full question tags only')
        else:
            _uuid(event.get('request_id'), 'request_id')
            if type(event.get('expected_revision')) is not int or event['expected_revision'] < 1:
                raise ValueError('expected_revision must be a positive integer')
            if operation == 'edit':
                _text(event.get('title'), 'title', 500)
                _text(event.get('body'), 'body', 24000)
            elif operation == 'answer_local':
                _text(event.get('text'), 'text', 24000)
                _text(event.get('source_message_id'), 'source_message_id', 200)
            else:
                _text(event.get('reason'), 'reason', 2000)
        return operation

    def apply(self, event):
        """Record exactly one explicit event, returning its immutable result.

        A local answer reports input already received in the originating
        conversation. It is not an instruction to send input and never runs
        an engine. Concurrent local/global lifecycle changes use revisions.
        """
        operation = self._validate(event)
        if operation == 'consume':
            from ccc_server.decision_session_answers import SessionAnswers
            return SessionAnswers(self.registry).consume(event)
        encoded = _json(event)
        with self.registry._transaction() as db:
            old = db.execute('SELECT payload,result FROM session_question_events WHERE event_id=?',
                             (event['event_id'],)).fetchone()
            if old:
                if old['payload'] != encoded:
                    raise SessionQuestionConflict('Event ID already used for another payload')
                return json.loads(old['result'])
            now = datetime.now(timezone.utc).isoformat()
            if operation == 'create':
                origin = event['session_id'] + ':' + event['question_id']
                previous = db.execute('SELECT 1 FROM requests WHERE adapter=? AND origin_id=? AND generation=?',
                                     ('session-question', origin, event['generation'])).fetchone()
                if previous:
                    raise SessionQuestionConflict('Question episode already exists; use an edit event')
                if db.execute("SELECT 1 FROM requests WHERE adapter='session-question' AND origin_id=? AND status='open'",
                              (origin,)).fetchone():
                    raise SessionQuestionConflict('Resolve or withdraw the preceding question episode first')
                content = dict(adapter='session-question', origin_id=origin,
                               generation=event['generation'], session_id=event['session_id'],
                               question_id=event['question_id'], title=event['title'], body=event['body'],
                               aliases=event.get('aliases', []), kind='question', wait_kind='owner',
                               visibility='local', created_at=now, source_revision=event['event_id'])
                result = self.registry._upsert(db, adapter='session-question', origin_id=origin,
                    generation=event['generation'], content=content, aliases=_aliases(content), status='open')
            else:
                row = db.execute('SELECT * FROM requests WHERE request_id=?',
                                 (event['request_id'],)).fetchone()
                if row is None or row['adapter'] != 'session-question':
                    raise SessionQuestionConflict('Session question not found')
                content = json.loads(row['content'])
                if content.get('session_id') != event['session_id']:
                    raise SessionQuestionConflict('Question belongs to another session')
                # An outbox save is provisional. Local input/edit/withdraw
                # can win until the source inbox CAS accepts a remote answer.
                if (row['revision'] != event['expected_revision'] or row['status'] != 'open'
                        or (row['answer_state'] != 'none' and not (
                            row['answer_state'] == 'recorded' and row['delivery_state'] in
                            ('pending', 'sending', 'unknown', 'rejected')))):
                    raise SessionQuestionConflict('Question changed or already resolved')
                status = 'open'
                if operation == 'edit':
                    content.update(title=event['title'], body=event['body'])
                elif operation == 'promote':
                    if content.get('visibility') != 'local':
                        raise SessionQuestionConflict('Question is already promoted')
                    content.update(visibility='global', promoted_at=now, promotion_reason=event['reason'])
                elif operation == 'answer_local':
                    status = 'answered'
                    content['local_answer'] = dict(text=event['text'], source_message_id=event['source_message_id'],
                                                   received_at=now, session_id=event['session_id'])
                elif operation == 'withdraw':
                    status = 'withdrawn'
                    content.update(withdrawn_at=now, withdrawal_reason=event['reason'])
                content['source_revision'] = event['event_id']
                result = self.registry._upsert(db, adapter=row['adapter'], origin_id=row['origin_id'],
                    generation=row['generation'], content=content, aliases=json.loads(row['aliases']), status=status)
                if operation == 'answer_local':
                    # The source reports an answer already received locally;
                    # no external delivery or completion is inferred.
                    db.execute("UPDATE requests SET answer_state='recorded', delivery_state='local' WHERE request_id=?",
                               (row['request_id'],))
                    result = self.registry._record(db.execute('SELECT * FROM requests WHERE request_id=?',
                                                              (row['request_id'],)).fetchone())
            db.execute('INSERT INTO session_question_events VALUES (?,?,?,?,?,?)',
                       (event['event_id'], result['request_id'], operation, encoded, now, _json(result)))
            return result

    def events(self, request_id):
        """Read audit records for one identity without parsing any transcript."""
        import sqlite3
        db = sqlite3.connect(str(self.registry.db_path), timeout=self.registry.timeout)
        db.row_factory = sqlite3.Row
        try:
            return [dict(event_id=row['event_id'], operation=row['operation'],
                         payload=json.loads(row['payload']), recorded_at=row['recorded_at'])
                    for row in db.execute('SELECT * FROM session_question_events WHERE request_id=? ORDER BY rowid',
                                          (request_id,))]
        finally:
            db.close()


def event_payload(payload, *, state_root=None):
    """HTTP-independent facade; writes only CCC state and sends no input."""
    import sqlite3
    from pathlib import Path
    from ccc_server import decision_inbox
    from ccc_server.decision_registry import DecisionRegistry
    try:
        root = Path(state_root) if state_root else decision_inbox._state_root()
        registry = DecisionRegistry(root / 'decision-inbox' / 'requests.sqlite3', timeout=1)
        result = SessionQuestions(registry).apply(payload)
        if payload.get('operation') == 'consume':
            return dict(ok=True, consumption=result, input_sent=False, work_completed=False), 200
        return dict(ok=True, request=result, input_sent=False), 200
    except SessionQuestionConflict as error:
        return dict(ok=False, error=str(error)), 409
    except (ValueError, TypeError) as error:
        return dict(ok=False, error=str(error)), 400
    except (OSError, sqlite3.Error):
        return dict(ok=False, error='Session question registry is unavailable'), 503


def session_payload(session_id, *, state_root=None):
    """Only explicitly registered events; no transcript scan or enrollment."""
    import sqlite3
    from pathlib import Path
    from ccc_server import decision_inbox
    from ccc_server.decision_registry import DecisionRegistry
    try:
        _uuid(session_id, 'session_id')
        root = Path(state_root) if state_root else decision_inbox._state_root()
        database = root / 'decision-inbox' / 'requests.sqlite3'
        if not database.exists():
            return dict(ok=True, requests=[], remote_answers=[], remote_answers_supported=True), 200
        from types import SimpleNamespace
        from ccc_server.decision_session_answers import SessionAnswerAdapter
        # GET must not construct a writer registry or initialize answer schema.
        reader = SessionAnswerAdapter(SimpleNamespace(db_path=database, timeout=1))
        with reader._read() as db:
            requests = [DecisionRegistry._record(row) for row in db.execute(
                "SELECT * FROM requests WHERE adapter='session-question' "
                "ORDER BY origin_id,generation")
                if json.loads(row['content']).get('session_id') == session_id]
        remote_answers = reader.poll(session_id)
        return dict(ok=True, requests=requests, remote_answers=remote_answers,
                    remote_answers_supported=True), 200
    except ValueError as error:
        return dict(ok=False, error=str(error)), 400
    except (OSError, sqlite3.Error):
        return dict(ok=False, requests=None, error='Session question registry is unavailable'), 503
