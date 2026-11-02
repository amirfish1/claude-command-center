"""Bind producer annotations to exact source requests, never to ticket tags.

Validation reads only CCC's already bound registry. Reserving a delivery owner
writes CCC metadata only. Neither operation refreshes a source, creates a
request, submits an answer, or transfers existing delivery ownership.
The retained link is frozen: edits to the source require a new report revision.
"""
import json
import sqlite3
from pathlib import Path


class SourceReportConflict(ValueError):
    """A producer is referring to an unknown or changed source question."""


def bind_source_request(value, *, database=None):
    required = {'version', 'adapter', 'origin_id', 'generation', 'source_revision',
                'source_snapshot', 'delivery_owner'}
    optional = {'request_id', 'request_revision'}
    if (not isinstance(value, dict) or set(value) - required - optional
            or required - set(value) or type(value['version']) is not int or value['version'] != 1
            or value['adapter'] != 'watchtower-block'
            or value['delivery_owner'] not in ('routine', 'ccc-outbox')
            or any(not isinstance(value[key], str) or not value[key]
                   for key in ('origin_id', 'generation', 'source_revision'))):
        raise SourceReportConflict('Invalid typed source request')
    snapshot = value['source_snapshot']
    if (not isinstance(snapshot, dict)
            or set(snapshot) != {'store_id', 'blocked_at', 'block_question', 'wait_kind', 'wait'}
            or snapshot['wait_kind'] != 'owner' or snapshot['blocked_at'] != value['generation']
            or not isinstance(snapshot['block_question'], str) or not snapshot['block_question'].strip()
            or (snapshot['wait'] is not None and not isinstance(snapshot['wait'], dict))):
        raise SourceReportConflict('Exact owner question snapshot required')
    if database is None:
        from ccc_server import decision_inbox
        database = decision_inbox._state_root() / 'decision-inbox' / 'requests.sqlite3'
    uri = Path(database).resolve().as_uri() + '?mode=ro'
    connection = sqlite3.connect(uri, uri=True, timeout=1)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('BEGIN')
        binding = connection.execute("SELECT value FROM metadata WHERE key='source_scope:watchtower-block'").fetchone()
        row = connection.execute('SELECT * FROM requests WHERE adapter=? AND origin_id=? AND generation=?',
                                 (value['adapter'], value['origin_id'], value['generation'])).fetchone()
        if (not binding or json.loads(binding[0])['scope']['store_id'] != snapshot['store_id']
                or row is None or row['status'] != 'open' or row['answer_state'] != 'none'):
            raise SourceReportConflict('Source request is not currently open in the bound store')
        content = json.loads(row['content'])
        if (content.get('kind') != 'question' or content.get('visibility') != 'global'
                or content.get('wait_kind') != 'owner'
                or content.get('source_revision') != value['source_revision']
                or content.get('source_snapshot') != snapshot
                or ('request_id' in value and value['request_id'] != row['request_id'])
                or ('request_revision' in value and (type(value['request_revision']) is not int
                                                    or value['request_revision'] != row['revision']))):
            raise SourceReportConflict('Source request changed; refresh coverage and report a new revision')
        owner = connection.execute('SELECT value FROM metadata WHERE key=?',
                                   ('delivery_owner:' + row['request_id'],)).fetchone()
        if owner and owner['value'] != value['delivery_owner']:
            raise SourceReportConflict('This question already belongs to another delivery owner')
        # Detach the producer object and return canonical identity from the
        # registry, not an ID supplied by the producer or inferred from Q.
        result = json.loads(json.dumps(value, allow_nan=False))
        result.update(request_id=row['request_id'], request_revision=row['revision'])
        return result
    finally:
        connection.close()


def reserve_delivery_owner(value, *, database=None):
    """Fence all reports and the outbox to one owner per canonical request.

    A reservation survives card-write failures, so a retry must use the same
    owner. Changing owners requires an explicit later coordinated handoff;
    deleting or rewriting a card never transfers pending answer obligations.
    """
    if database is None:
        from ccc_server import decision_inbox
        database = decision_inbox._state_root() / 'decision-inbox' / 'requests.sqlite3'
    connection = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=rw', uri=True, timeout=1)
    try:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT revision,status,answer_state FROM requests WHERE request_id=?',
                                 (value['request_id'],)).fetchone()
        if row != (value['request_revision'], 'open', 'none'):
            raise SourceReportConflict('Source request changed before delivery ownership was reserved')
        key = 'delivery_owner:' + value['request_id']
        connection.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)', (key, value['delivery_owner']))
        if connection.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()[0] != value['delivery_owner']:
            raise SourceReportConflict('This question already belongs to another delivery owner')
        connection.commit()
    finally:
        connection.close()
