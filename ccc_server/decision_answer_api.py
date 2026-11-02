"""Inert controller contract for future HTTP wiring; no routes or dispatcher.

Dependencies are explicit: a registry, a read-only full-export callable and a
conditional source adapter. No executable discovery or private configuration.
"""
import json
import os
import sqlite3
import uuid

from ccc_server.decision_delivery import AnswerConflict, AnswerOutbox
from ccc_server.decision_sources import apply_owner_export

MAX_BODY_BYTES = 128 * 1024
SERVER_ACTOR = 'owner'
OPT_IN = 'CCC_DECISION_ANSWERS_ENABLED'


class AnswerAPIError(ValueError):
    def __init__(self, status, code):
        self.status = status
        self.code = code
        super().__init__(code)

    def as_dict(self):
        return {'ok': False, 'error': self.code}


def _uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def parse_request(body, *, action='record'):
    """Parse bounded UTF-8 JSON, rejecting unknown fields and duplicate keys."""
    if not isinstance(body, (bytes, str)):
        raise AnswerAPIError(400, 'invalid_request')
    try:
        encoded = body.encode('utf-8') if isinstance(body, str) else body
        if len(encoded) > MAX_BODY_BYTES:
            raise AnswerAPIError(413, 'body_too_large')
        data = json.loads(encoded.decode('utf-8'), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, RecursionError) as error:
        if isinstance(error, AnswerAPIError):
            raise
        raise AnswerAPIError(400, 'invalid_request') from error
    fields = {'request_id', 'expected_revision', 'text', 'idempotency_key'}
    if action in ('send', 'reconcile'):
        fields = {'answer_id'}
    elif action != 'record':
        raise AnswerAPIError(400, 'invalid_action')
    if not isinstance(data, dict) or set(data) != fields:
        raise AnswerAPIError(400, 'invalid_request')
    if action != 'record':
        valid = _uuid(data['answer_id'])
    else:
        valid = (_uuid(data['request_id']) and type(data['expected_revision']) is int
                 and 0 < data['expected_revision'] <= 2**63 - 1
                 and isinstance(data['text'], str) and bool(data['text'].strip())
                 and len(data['text']) <= 24000
                 and isinstance(data['idempotency_key'], str)
                 and bool(data['idempotency_key'].strip())
                 and len(data['idempotency_key']) <= 200)
        # Lone surrogate escapes cannot become a valid UTF-8 frozen payload.
        try:
            data['text'].encode('utf-8')
            data['idempotency_key'].encode('utf-8')
        except (UnicodeError, AttributeError):
            valid = False
    if not valid:
        raise AnswerAPIError(400, 'invalid_request')
    return data


class ControllerOutbox(AnswerOutbox):
    """Read-only API extension; every state transition stays in AnswerOutbox.

    A future shared get_by_key method can replace this extension. The lookup
    is only an optimization: record() still arbitrates concurrent keys and
    delivery ownership atomically, including any race after this read.
    """
    def get_by_key(self, key):
        with self.registry._transaction() as db:
            return self._row(db.execute('SELECT * FROM answers WHERE idempotency_key=?',
                                        (key,)).fetchone())


def _response(row, *, replay=False):
    # Internal source snapshots and attempt tokens are not HTTP overrides.
    return {'ok': True, 'answer_id': row['answer_id'], 'request_id': row['request_id'],
            'state': row['state'], 'answer': row['payload']['answer'],
            'receipt': row['receipt'], 'replay': replay, 'work_completed': False}


class DecisionAnswerAPI:
    """Methods return JSON-compatible dicts or raise AnswerAPIError.

    The opt-in is frozen at construction. Even enabled instances do nothing
    until a method is called. Construct dependencies explicitly; no production
    sources are selected here. record never sends, status never reconciles.
    """
    def __init__(self, registry, *, export_source=None, adapter=None, environ=None):
        self.enabled = (os.environ if environ is None else environ).get(OPT_IN) == '1'
        self.registry = registry
        self.export_source = export_source
        self.adapter = adapter
        self.outbox = ControllerOutbox(registry) if self.enabled else None

    def _enabled(self):
        if not self.enabled:
            raise AnswerAPIError(503, 'answers_disabled')

    def record(self, body):
        self._enabled()
        data = parse_request(body)
        try:
            old = self.outbox.get_by_key(data['idempotency_key'])
            if old:
                if old['payload']['answer'] != dict(data_without_key(data), actor=SERVER_ACTOR):
                    raise AnswerAPIError(409, 'answer_conflict')
                return _response(old, replay=True)
            with self.registry._transaction() as db:
                request = db.execute('SELECT adapter FROM requests WHERE request_id=?',
                                     (data['request_id'],)).fetchone()
            session_source = request is not None and request['adapter'] == 'session-question'
            if not session_source and self.export_source is None:
                raise AnswerAPIError(503, 'source_unavailable')
            try:
                # A failed refresh is never substituted with an empty export
                # or the last successful registry projection.
                if not session_source:
                    refreshed = apply_owner_export(self.export_source(), registry=self.registry, cards={})
                    if refreshed.get('ok') is not True:
                        raise ValueError('Incomplete source coverage')
            except sqlite3.Error:
                raise
            except Exception as error:
                raise AnswerAPIError(503, 'source_unavailable') from error
            row = self.outbox.record(**data_without_key(data),
                                     idempotency_key=data['idempotency_key'], actor=SERVER_ACTOR)
            return _response(row)
        except AnswerConflict as error:
            raise AnswerAPIError(409, 'answer_conflict') from error
        except sqlite3.Error as error:
            raise AnswerAPIError(503, 'registry_unavailable') from error

    def status(self, answer_id):
        self._enabled()
        if not _uuid(answer_id):
            raise AnswerAPIError(400, 'invalid_request')
        try:
            row = self.outbox.get(answer_id)
        except sqlite3.Error as error:
            raise AnswerAPIError(503, 'registry_unavailable') from error
        if row is None:
            raise AnswerAPIError(404, 'answer_not_found')
        return _response(row)

    def act(self, action, body):
        self._enabled()
        data = parse_request(body, action=action)
        if action not in ('send', 'reconcile'):
            raise AnswerAPIError(400, 'invalid_action')
        current = self.status(data['answer_id'])
        # Accepted/rejected are terminal; unknown/sending require receipt-first
        # reconciliation. No action ever combines reconciliation with retry.
        eligible = ('pending',) if action == 'send' else ('unknown', 'sending')
        if current['state'] not in eligible:
            return current
        if not getattr(self.adapter, 'conditional_idempotent', False):
            raise AnswerAPIError(503, 'source_unavailable')
        try:
            method = self.outbox.dispatch if action == 'send' else self.outbox.reconcile
            return _response(method(data['answer_id'], self.adapter))
        except sqlite3.Error as error:
            raise AnswerAPIError(503, 'registry_unavailable') from error


def data_without_key(data):
    return {key: value for key, value in data.items() if key != 'idempotency_key'}
