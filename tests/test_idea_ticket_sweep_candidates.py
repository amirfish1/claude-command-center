"""Historical rows must not parse provenance; eligible helpers stay excluded."""

from ccc_server import idea_tickets as tickets


def test_sweep_checks_only_candidates_before_any_ticket_effect(monkeypatch):
    state = {
        'sessions': {
            'parent': {'ref': 'TEST-1'},
            'known': {'ref': 'TEST-1'},
            'other': {'skip': 'other'},
            'retry-helper': {'skip': 'not a UI spawn'},
            'retry-real': {'skip': 'not a UI spawn'},
        },
        'pending': {'pending-helper': {}, 'pending-real': {}, 'no-reply': {}},
        'tickets': {},
    }
    rows = [
        {'session_id': 'known', 'last_event_type': 'result'},
        {'session_id': 'unrelated', 'last_event_type': 'result'},
        {'session_id': 'other', 'parent_session_id': 'parent', 'last_event_type': 'result'},
        {'session_id': 'no-reply'},
        {'session_id': 'agent-helper', 'continued_from_session_id': 'parent'},
        {'session_id': 'pending-helper', 'session_cwd': '/tmp/te-side-check', 'last_event_type': 'result'},
        {'session_id': 'continuing-helper', 'slug': '-tmp-wt-model-check', 'continued_from_session_id': 'parent'},
        {'session_id': 'child-helper', 'session_cwd': '/tmp/wt-model-check',
         'parent_session_id': 'parent', 'last_event_type': 'result'},
        {'session_id': 'retry-helper', 'session_cwd': '/tmp/te-side-check',
         'continued_from_session_id': 'parent'},
        {'session_id': 'pending-real', 'session_cwd': '/tmp/project',
         'display_name': 'te-side-check', 'last_event_type': 'result'},
        {'session_id': 'retry-real', 'session_cwd': '/tmp/project',
         'continued_from_session_id': 'parent'},
    ]
    checked, filed, title_rows = [], [], []
    guard = tickets._plugin_side_call

    def provenance(**options):
        checked.append(options['row']['session_id'])
        return guard(**options)

    def turn_end(sid, **options):
        filed.append((sid, options))
        return {'queued': False}

    monkeypatch.setattr(tickets, '_enabled', lambda: True)
    monkeypatch.setattr(tickets, 'load_state', lambda: state)
    monkeypatch.setattr(tickets, '_inflight', set())
    monkeypatch.setattr(tickets, '_plugin_side_call', provenance)
    monkeypatch.setattr(tickets, 'on_turn_end', turn_end)
    monkeypatch.setattr(tickets, '_sweep_titles', lambda rows, data: title_rows.append((rows, data)))
    assert tickets.sweep_rows(rows) == 0
    assert checked == ['pending-helper', 'continuing-helper', 'child-helper',
                       'retry-helper', 'pending-real', 'retry-real']
    assert [sid for sid, _ in filed] == ['pending-real', 'retry-real']
    assert not filed[0][1]['retry_skip'] and filed[1][1]['retry_skip']
    assert title_rows == [(rows, state)]
