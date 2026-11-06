"""One read model for explicitly global requests, without transcript enrollment.

The source refresh may update CCC's projection, never the source. Reading the
registry itself uses a single read-only transaction. Unavailable coverage keeps
the last observed questions visible and never becomes a verified empty inbox.
"""
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ccc_server.decision_coverage import wait_kind
from ccc_server.decision_registry import CLOSED_PREFIX, DecisionRegistry

_LEADING_TAG = re.compile(r'\s*(\[Q\d+:[a-z0-9-]+\])')


def read_requests(database, *, answers_enabled=False, source_current=False):
    database = Path(database)
    if not database.exists():
        return []
    connection = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('BEGIN')
        metadata = {r['key']: r['value'] for r in connection.execute('SELECT * FROM metadata')}
        binding = json.loads(metadata.get('source_scope:watchtower-block', 'null'))
        store_id = binding['scope']['store_id'] if binding else None
        answers = {}
        consumed = {}
        has_session_events = connection.execute("SELECT 1 FROM sqlite_master WHERE name='session_question_events'").fetchone()
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='session_answer_receipts'").fetchone():
            consumed = {r['answer_id']: bool(r['consumed']) for r in connection.execute('SELECT answer_id,consumed FROM session_answer_receipts')}
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='answers'").fetchone():
            for saved_answer in connection.execute('SELECT * FROM answers ORDER BY rowid'):
                frozen = json.loads(saved_answer['payload'])['answer']
                answers[saved_answer['request_id']] = dict(answer_id=saved_answer['answer_id'],
                    state=saved_answer['state'], text=frozen['text'],
                    expected_revision=frozen['expected_revision'])
                if saved_answer['answer_id'] in consumed:
                    answers[saved_answer['request_id']]['session_consumed'] = consumed[saved_answer['answer_id']]
        result = []
        for saved in connection.execute('SELECT * FROM requests ORDER BY adapter,origin_id,generation'):
            record = DecisionRegistry._record(saved)
            content = record['content']
            body = content.get('body') or ''
            if content.get('visibility') != 'global' or record['adapter'] == 'legacy-card':
                continue
            if record['adapter'] == 'watchtower-block':
                snapshot = content.get('source_snapshot') or {}
                if not store_id or snapshot.get('store_id') != store_id:
                    continue  # A display-cache observation is not source coverage.
                question = content.get('kind') == 'question' and content.get('wait_kind') == 'owner'
                body = snapshot.get('block_question', body)
                observed_at = snapshot.get('blocked_at') or record['generation']
            elif record['adapter'] == 'session-question':
                question = content.get('kind') == 'question'
                observed_at = content.get('created_at') or ''
            else:
                continue  # Reviews/providers need their own complete source contract.
            if CLOSED_PREFIX + record['request_id'] in metadata:
                record['status'] = 'closed'  # The owner ended it; refreshes must not revive it.
            needs_answer = (question and record['status'] == 'open'
                            and (record['answer_state'] == 'none' or record['delivery_state'] == 'rejected'))
            delivery_owner = metadata.get('delivery_owner:' + record['request_id'])
            session_source = record['adapter'] == 'session-question'
            promoted = bool(session_source and has_session_events and connection.execute(
                "SELECT 1 FROM session_question_events WHERE request_id=? AND operation='promote'",
                (record['request_id'],)).fetchone())
            current_source = session_source or source_current
            can_answer = bool(answers_enabled and current_source and needs_answer
                              and (record['adapter'] == 'watchtower-block' or promoted)
                              and delivery_owner in (None, 'ccc-outbox'))
            can_close = bool(answers_enabled and current_source and needs_answer
                             and delivery_owner in (None, 'ccc-outbox'))
            if not answers_enabled:
                unavailable_reason = 'Source answers are not enabled in this phase.'
            elif delivery_owner == 'routine':
                unavailable_reason = 'This answer is handled by its reporting routine.'
            elif not current_source:
                unavailable_reason = 'Check source coverage before saving an answer.'
            elif session_source and not promoted:
                unavailable_reason = 'This session question has no explicit promotion event.'
            elif not question:
                unavailable_reason = 'This approval has no answer adapter.'
            else:
                unavailable_reason = ''
            result.append(dict(request_id=record['request_id'], revision=record['revision'],
                adapter=record['adapter'], origin_id=record['origin_id'], generation=record['generation'],
                title=content.get('title') or record['origin_id'], body=body,
                aliases=record['aliases'], kind=content.get('kind') or '', status=record['status'],
                session_id=content.get('session_id') if record['adapter'] == 'session-question' else None,
                episode_provenance=content.get('episode_provenance'),
                answer_state=record['answer_state'], delivery_state=record['delivery_state'],
                delivery_owner=delivery_owner, answer=answers.get(record['request_id']),
                source_current=bool(current_source), reply_supported=bool(not session_source or promoted),
                needs_answer=needs_answer, observed_at=observed_at, can_answer=can_answer, can_close=can_close,
                answer_unavailable_reason=unavailable_reason))
        return result
    finally:
        connection.close()


def github_requests(tickets):
    """Owner waits on GitHub-backed tickets, which the local export can't see.

    Read-through only: they never enter the registry and have no conditional
    answer contract, so they are answered on the ticket itself.
    """
    rows = []
    for item in tickets or []:
        ref, body = item.get('ref'), str(item.get('block_question') or '').strip()
        if (not str(item.get('id') or '').startswith('github:') or not ref or not body
                or wait_kind(item) != 'owner'):
            continue
        blocked_at = str(item.get('blocked_at') or '')
        tag = _LEADING_TAG.match(body)
        rows.append(dict(
            request_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f'ccc-github-wait:{ref}:{blocked_at}')),
            revision=1, adapter='watchtower-github', origin_id=ref, generation=blocked_at,
            title=item.get('title') or ref, body=body, aliases=[tag.group(1)] if tag else [],
            kind='question', status='open', session_id=None, episode_provenance=None,
            answer_state='none', delivery_state='none', delivery_owner=None, answer=None,
            source_current=True, reply_supported=False, needs_answer=True, observed_at=blocked_at,
            can_answer=False, can_close=False,
            answer_unavailable_reason='Open the ticket to answer this GitHub-backed question.'))
    return rows


def requests_payload(*, state_root=None, source_loader=None, tickets=None):
    from ccc_server import decision_inbox
    from ccc_server.decision_sources import owner_source_payload
    root = Path(state_root) if state_root else decision_inbox._state_root()
    refreshed = (source_loader or owner_source_payload)(state_root=root)
    source = refreshed.get('source') or {'status': 'unavailable', 'complete': False}
    current = refreshed.get('ok') is True and source.get('authoritative') is True and source.get('complete') is True
    from ccc_server.decision_answer_settings import answer_environment
    enabled = answer_environment(root).get('CCC_DECISION_ANSWERS_ENABLED') == '1'
    try:
        rows = read_requests(root / 'decision-inbox' / 'requests.sqlite3',
                             answers_enabled=enabled, source_current=current)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return dict(ok=False, requests=None, needs_answer_count=None, source=source,
                    error='Cannot read saved requests. Retry the source check.', llm_used=False,
                    answers_enabled=enabled)
    rows += github_requests(tickets)
    rows.sort(key=lambda r: (r['adapter'].startswith('watchtower'), r['origin_id']))
    issues = [dict(origin_id=r.get('origin_id'), title=r.get('title'), body=r.get('body'),
                   reason='Source question has no stable episode; it has not been imported.')
              for r in (refreshed.get('requests') or []) if not r.get('generation')]
    return dict(ok=True, requests=rows, needs_answer_count=sum(r['needs_answer'] for r in rows) if current else None,
                source_current=current, source=source, llm_used=False, answers_enabled=enabled,
                coverage_issues=issues,
                checked_at=datetime.now(timezone.utc).isoformat(),
                error=refreshed.get('error') if not current else None,
                limitations=['Coverage: local file-backed owner waits, explicitly promoted session questions and GitHub-backed owner waits.',
                             'GitHub-backed questions come from the ticket cache and are answered on the ticket.',
                             'Ordinary interactive questions stay in their sessions.',
                             'Reviews, other remote queues and untyped reports have separate lifecycles.'])
