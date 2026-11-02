"""Paid admission is durable and independent of browser timing."""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
import server
from ccc_server import session_rechecks as recaps

NOW = 1800000000


def row(**changes):
    value = dict(session_id='old-session', modified=NOW - 3 * recaps.DAY,
                 last_event_type='assistant', state='idle', session_state={
                     'did': 'Shipped the layout.', 'next_step_user': 'Choose the export format.',
                     'completion': {'scope': {'goal': 'Readable exports'}, 'remaining_actions': []}})
    value.update(changes)
    return value


def item(sid='old-session', revision='v1', chain=None):
    return dict(session_id=sid, chain=chain or sid, revision=revision,
                recap={'goal': 'Readable exports', 'request': 'Choose the format.'})


def test_candidate_requires_an_old_current_owner_request():
    assert recaps.candidate(row(), now=NOW)['recap']['goal'] == 'Readable exports'
    for change in [dict(modified=NOW - 60), dict(state='working'), dict(last_event_type='user'),
                   dict(archived=True), dict(trashed=True), dict(pending_tool='Read')]:
        assert recaps.candidate(row(**change), now=NOW) is None
    state = {'next_step_user': 'None.', 'completion': {'remaining_actions': [
        {'owner': 'agent', 'text': 'Deliver the export.'}, {'owner': 'user', 'text': 'Review it later.'}]}}
    assert recaps.candidate(row(session_state=state), now=NOW) is None
    state['completion']['remaining_actions'].reverse()
    assert recaps.candidate(row(session_state=state), now=NOW)


def test_revision_ignores_view_changes_but_tracks_requests_and_ticket_evidence():
    first = recaps.candidate(row(), now=NOW)
    assert recaps.candidate(row(title='A new label', modified=NOW - 2 * recaps.DAY), now=NOW)['revision'] == first['revision']
    assert recaps.candidate(row(), tickets=[{'ref': 'TASK-1', 'status': 'closed'}], now=NOW)['revision'] != first['revision']
    assert recaps.candidate(row(session_state={'next_step_user': 'Choose another format.'}), now=NOW)['revision'] != first['revision']


def test_concurrent_tabs_and_restart_admit_once(tmp_path):
    store = recaps.Store(tmp_path / 'checks.db')
    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = list(pool.map(lambda _: store.admit(item(), NOW)[1], range(16)))
    assert sum(admitted) == 1
    assert recaps.Store(store.path).admit(item(), NOW + 1)[1] is False
    store.finish(item(), {'finding': 'Still pending'})
    cached, admitted = recaps.Store(store.path).admit(item(), NOW + 20 * recaps.DAY)
    assert not admitted and cached['result']['finding'] == 'Still pending'


def test_changed_revision_cooldown_and_chain_survive_continuation(tmp_path):
    store = recaps.Store(tmp_path / 'checks.db')
    store.admit(item(chain='root'), NOW)
    store.finish(item(chain='root'), {})
    changed = item('successor', 'v2', chain='root')
    assert store.admit(changed, NOW + 60)[0]['status'] == 'cooldown'
    assert store.admit(changed, NOW + recaps.DAY)[1]


def test_one_checker_and_three_checks_per_utc_day(tmp_path):
    store = recaps.Store(tmp_path / 'checks.db')
    day = (NOW // recaps.DAY) * recaps.DAY + 60
    store.admit(item('one'), day)
    assert store.admit(item('two'), day)[0]['status'] == 'queued'
    store.finish(item('one'), {})
    for sid in ('two', 'three'):
        assert store.admit(item(sid), day)[1]
        store.finish(item(sid), {})
    assert store.admit(item('four'), day)[0]['status'] == 'daily_limit'
    assert store.admit(item('four'), day + recaps.DAY)[1]


def test_lost_process_becomes_unverified_without_retry(tmp_path):
    store = recaps.Store(tmp_path / 'checks.db')
    store.admit(item(), NOW)
    response, admitted = recaps.Store(store.path).admit(item(), NOW + 181)
    assert response['status'] == 'unverified' and not admitted


def test_message_identity_migration_reuses_paid_result_once(tmp_path):
    store = recaps.Store(tmp_path / 'checks.db')
    store.admit(item(revision='old-content-key'), NOW)
    store.finish(item(revision='old-content-key'), {'finding': 'Already checked'})
    upgraded = dict(item(revision='message-one'), legacy_revision='old-content-key')
    response, admitted = store.admit(upgraded, NOW + 60)
    assert not admitted and response['result']['finding'] == 'Already checked'
    changed = dict(upgraded, revision='message-two')
    assert store.admit(changed, NOW + 60)[0]['status'] == 'cooldown'
    assert store.admit(changed, NOW + recaps.DAY)[1]


@pytest.mark.parametrize('phase', ['before', 'during', 'failure'])
def test_answer_or_failure_does_not_publish_stale_result_or_retry(tmp_path, monkeypatch, phase):
    store = recaps.Store(tmp_path / 'checks.db')
    store.admit(item(), NOW)
    loader = Mock(side_effect=[None] if phase == 'before' else [item(), None])
    checker = Mock(return_value={'finding': 'Pending'})
    if phase == 'failure':
        checker.side_effect = RuntimeError('Failed')
    monkeypatch.setattr(recaps, 'evidence_for', lambda value: {})
    recaps._work(store, item(), loader, checker)
    response, admitted = store.admit(item(), NOW + recaps.DAY)
    assert response['status'] == 'unverified' and response['result'] is None and not admitted
    assert checker.call_count == (0 if phase == 'before' else 1)


def test_observation_returns_saved_recap_and_reserves_before_launch(tmp_path, monkeypatch):
    policy = tmp_path / 'session-rechecks' / 'policy.json'
    policy.parent.mkdir()
    policy.write_text('{"enabled":true}')
    jobs = []
    response, status = recaps.observe({'session_ids': ['one', 'two']}, state_root=tmp_path,
        loader=lambda sid: item(sid), launch=jobs.append)
    assert status == 200
    assert response['sessions']['one']['status'] == 'checking'
    assert response['sessions']['two']['status'] == 'queued'
    assert len(jobs) == 1
    response, _ = recaps.observe({'session_ids': ['one']}, state_root=tmp_path,
        loader=lambda sid: item(sid), launch=jobs.append)
    assert len(jobs) == 1


def test_paid_checks_are_opt_in_and_saved_recaps_still_work(tmp_path):
    launch = Mock()
    response, status = recaps.observe({'session_ids': ['one']}, state_root=tmp_path,
        loader=lambda sid: item(sid), launch=launch)
    assert status == 200 and response['sessions']['one']['status'] == 'disabled'
    assert response['sessions']['one']['recap']['goal'] == 'Readable exports'
    launch.assert_not_called()


@pytest.mark.parametrize('payload', [None, {}, {'session_ids': []}, {'session_ids': ['../../escape']},
                                     {'session_ids': ['x'] * 21}])
def test_bad_observations_never_launch(payload, tmp_path):
    launch = Mock()
    assert recaps.observe(payload, state_root=tmp_path, launch=launch)[1] == 400
    launch.assert_not_called()


def test_checker_is_fresh_tool_free_bounded_and_does_not_retry(monkeypatch):
    monkeypatch.setattr(server, '_model_policy_blocks', lambda model: False)
    monkeypatch.setattr(server, '_resolve_claude_bin', lambda: {'available': True, 'bin': '/fake/claude'})
    result = dict(goal='Exports', shipped='Reported shipped', needs_you='Choose a format', finding='Pending')
    run = Mock(return_value=Mock(returncode=0, stdout=json.dumps({'result': json.dumps(result)})))
    monkeypatch.setattr(recaps.subprocess, 'run', run)
    assert recaps.run_checker({'text': 'x' * 30000}) == result
    args, options = run.call_args
    argv = args[0]
    assert argv[argv.index('--tools') + 1] == ''
    assert argv[argv.index('--max-turns') + 1] == '1'
    assert argv[argv.index('--max-budget-usd') + 1] == '0.08'
    assert '--no-session-persistence' in argv and '--resume' not in argv
    assert options['timeout'] == 60 and len(options['input']) < 18000
    run.return_value.stdout = 'bad json'
    with pytest.raises(ValueError):
        recaps.run_checker({})
    assert run.call_count == 2


def test_loader_checks_current_transcript_and_rejects_answered_or_superseded_rows(tmp_path, monkeypatch):
    from ccc_server import lineage, ship_graph, continuation
    source = tmp_path / 'old.jsonl'
    source.write_text('transcript')
    import os
    os.utime(source, (NOW - 3 * recaps.DAY, NOW - 3 * recaps.DAY))
    monkeypatch.setattr(recaps, '_known_row', lambda sid: row())
    monkeypatch.setattr(recaps.time, 'time', lambda: NOW)
    monkeypatch.setattr(server, '_load_conversation_lifecycle_sets', lambda: (set(), set()))
    monkeypatch.setattr(server, '_sessions_state_snapshot', lambda: {})
    monkeypatch.setattr(server, 'conversation_transcript_path', lambda sid: str(source))
    monkeypatch.setattr(server, '_wt_list_items_snapshot_only', lambda: [])
    monkeypatch.setattr(server._idea_tickets, 'idea_ticket_rows_map', lambda: {})
    monkeypatch.setattr(ship_graph, '_get_connection', lambda: None)
    monkeypatch.setattr(lineage, 'latest_successor', lambda conn, sid: sid)
    monkeypatch.setattr(lineage, 'continuation_chain_members', lambda conn, sid: ['chain-root', sid])
    monkeypatch.setattr(continuation, 'manual_forward_target', lambda sid: sid)
    events = [{'type': 'assistant', 'blocks': [{'type': 'text', 'text':
        '<session-state>\nDID: Shipped.\nNEXT_STEP_USER: Choose PDF or CSV.\n</session-state>'}]}]
    parser = Mock(side_effect=lambda *a, **k: {'events': events})
    monkeypatch.setattr(server, 'parse_conversation', parser)
    loaded = recaps.load_candidate('old-session')
    assert loaded['recap']['request'] == 'Choose PDF or CSV.'
    assert loaded['chain'] == 'chain-root'
    parser.assert_called_with('old-session', tail=80, use_cache=True)
    from ccc_server import session_brief
    monkeypatch.setattr(recaps, '_known_row', lambda sid: None)
    monkeypatch.setattr(session_brief, '_session_meta_row', lambda conn, sid: {'end_ts': NOW - 3 * recaps.DAY})
    monkeypatch.setattr(recaps, '_indexed_doc', lambda sid: {'title': 'Indexed old session'})
    assert recaps.load_candidate('old-session')['recap']['request'] == 'Choose PDF or CSV.'
    events.append({'type': 'user', 'text': 'CSV'})
    assert recaps.load_candidate('old-session') is None
    events.pop()
    monkeypatch.setattr(lineage, 'latest_successor', lambda conn, sid: 'new-session')
    assert recaps.load_candidate('old-session') is None


def test_archive_index_is_reused_across_visible_rows(monkeypatch):
    class Once(list):
        def __iter__(self):
            self.calls = getattr(self, 'calls', 0) + 1
            assert self.calls == 1, 'archive was rescanned per recap row'
            return super().__iter__()
    rows = Once([row(), row(session_id='second')])
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE', {'fixture': {'cached_at': 10, 'conversations': rows}})
    monkeypatch.setattr(recaps, '_rows_token', None)
    assert recaps._known_row('old-session')['session_id'] == 'old-session'
    assert recaps._known_row('second')['session_id'] == 'second'


def test_recap_text_read_never_refreshes_the_global_index(tmp_path, monkeypatch):
    from ccc_server import session_fts
    import sqlite3
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE sdoc (session_id TEXT, title TEXT, prompts TEXT, report TEXT)')
    conn.execute('INSERT INTO sdoc VALUES (?,?,?,?)', ('old', 'Old work', 'User ask', 'Last reply'))
    monkeypatch.setattr(session_fts, '_get_connection', lambda: conn)
    monkeypatch.setattr(session_fts, 'sdoc_rowids_sql', lambda count: 'session_id=?')
    refresh = Mock(side_effect=AssertionError('Recap triggered global refresh'))
    monkeypatch.setattr(session_fts, '_sync_index', refresh)
    assert recaps._indexed_doc('old')['prompts'] == 'User ask'
    refresh.assert_not_called()
