"""Session activity is observed, durable, and separate from lane ancestry."""
import json

import server
from ccc_server import session_activity as activity
from ccc_server import session_driver


def write(path, rows, mode='w'):
    with path.open(mode) as handle:
        for row in rows:
            handle.write(json.dumps(row) + '\n')


def user(text, at, ident=''):
    return {'type': 'user', 'uuid': ident or at, 'timestamp': at,
            'message': {'content': text}}


def assistant(at, output=5):
    return {'type': 'assistant', 'timestamp': at, 'message': {'id': 'request-a',
            'usage': {'input_tokens': 10, 'output_tokens': output,
                      'cache_read_input_tokens': 20}}}


def test_continuations_preserve_lane_map_and_survive_two_writers(tmp_path):
    target = tmp_path / 'graph.json'
    first = server._SessionGraph(target)
    second = server._SessionGraph(target)
    first.add_edge('launcher', 'successor')
    first.save()
    second.load()
    first.add_continuation('origin', 'successor', 100)
    first.note_ticket_session('successor')
    first.save()
    second.record_activity([{'id': 'wake', 'type': 'wake', 'receiver': 'successor', 'at': 101}])
    reopened = server._SessionGraph(target)
    reopened.load()
    assert reopened.parent_of('successor') == 'launcher'
    assert reopened.lineage_snapshot()['successor']['parent'] == 'origin'
    assert reopened.ticket_sessions() == {'successor'}
    assert reopened.latest_activity('successor')['id'] == 'wake'


def test_native_usage_is_incremental_and_does_not_count_streaming_twice(tmp_path, monkeypatch):
    graph = server._SessionGraph(tmp_path / 'graph.json')
    path = tmp_path / 'session.jsonl'
    write(path, [user('Owner ask', '2026-01-01T00:00:00Z'),
                 assistant('2026-01-01T00:00:01Z'),
                 assistant('2026-01-01T00:00:02Z', 8),
                 user('[WATCHTOWER] Comment', '2026-01-01T01:00:00Z')])
    activity.index_transcript(graph, 'session', path)
    events = graph.activity_snapshot({'session'})
    assert len(events) == 2
    assert events[0]['tokens'] == {'input': 10, 'output': 8, 'cache_read': 20, 'cache_write': 0}
    assert events[1]['tokens'] is None
    original = type(path).open
    def forbidden(self, *args, **kwargs):
        assert self != path, 'unchanged transcripts must not be reopened'
        return original(self, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(type(path), 'open', forbidden)
        activity.index_transcript(graph, 'session', path)
    write(path, [user('<system-reminder>Hook</system-reminder>', '2026-01-01T02:00:00Z'),
                 user('Next ask', '2026-01-01T03:00:00Z')], 'a')
    activity.index_transcript(graph, 'session', path)
    assert len(graph.activity_snapshot({'session'})) == 3
    meta = {'turn_driver': {'kind': 'service', 'at': 1}}
    monkeypatch.setattr(server, '_session_graph', graph)
    assert session_driver.fields(meta, 'session')['turn_driver']['kind'] == 'human'


def test_codex_mirrors_and_repeated_cumulative_usage_are_not_double_counted(tmp_path):
    graph = server._SessionGraph(tmp_path / 'graph.json')
    path = tmp_path / 'rollout.jsonl'
    def event(kind, payload, second):
        return {'type': kind, 'timestamp': f'2026-01-01T00:00:0{second}Z', 'payload': payload}
    usage = {'type': 'token_count', 'info': {'total_token_usage': {
        'input_tokens': 100, 'cached_input_tokens': 40, 'output_tokens': 10}}}
    write(path, [event('event_msg', {'type': 'user_message', 'message': 'Do work'}, 0),
                 event('response_item', {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Do work'}]}, 0),
                 event('event_msg', usage, 1), event('event_msg', usage, 2)])
    activity.index_transcript(graph, 'session', path)
    events = graph.activity_snapshot({'session'})
    assert len(events) == 1
    assert events[0]['tokens'] == {'input': 60, 'cache_read': 40, 'output': 10, 'cache_write': 0}


def test_envelope_identity_is_a_link_not_just_a_label(tmp_path):
    import sender_envelope
    attr = sender_envelope.make('receiver', who=sender_envelope.author('session', 'source', 'Builder'))
    text = sender_envelope.HEADER + json.dumps(attr) + ']\nContinue'
    result = activity.wake_event('receiver', text, 100, {}, 'fallback')
    assert result['sender'] == 'source'
    assert result['message_id'] == attr['message_id']
    assert result['sender_kind'] == 'session'
    assert 'text' not in result


def test_message_sends_are_not_wakes_and_do_not_replace_badges(tmp_path, monkeypatch):
    import sender_envelope
    attr = sender_envelope.make('target', who=sender_envelope.author('service', 'scheduler', 'Scheduler'))
    base = tmp_path / 'source'
    (base / 'attribution').mkdir(parents=True)
    write(base / 'attribution/2026-01.jsonl', [{'at': 100, 'attr': attr, 'event': 'send'},
                                            {'at': 101, 'attr': attr, 'event': 'send'}])
    monkeypatch.setattr(sender_envelope, '_ccc_state', lambda: base)
    monkeypatch.setattr(sender_envelope, '_wt_state', lambda: tmp_path / 'empty')
    graph = server._SessionGraph(tmp_path / 'graph.json')
    activity.index_messages(graph, {'target'})
    records = graph.activity_snapshot({'target'})
    assert len(records) == 1
    assert records[0]['type'] == 'message'
    assert records[0]['sender'] == 'scheduler'
    assert graph.latest_activity('target') == {}


def test_replay_after_restart_does_not_duplicate_inputs(tmp_path):
    graph = server._SessionGraph(tmp_path / 'graph.json')
    path = tmp_path / 'rollout.jsonl'
    write(path, [{'type': 'response_item', 'timestamp': '2026-01-01T00:00:00Z',
                  'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Do work'}]}}])
    activity.index_transcript(graph, 'session', path)
    activity._scan_cache.clear()
    reopened = server._SessionGraph(graph._path)
    reopened.load()
    activity.index_transcript(reopened, 'session', path)
    assert len(reopened.activity_snapshot({'session'})) == 1


def test_stale_writer_preserves_newer_streaming_usage(tmp_path):
    graph = server._SessionGraph(tmp_path / 'graph.json')
    graph.record_activity([{'id': 'wake', 'type': 'wake', 'receiver': 'session', 'at': 100,
                            'tokens': {'input': 10, 'output': 5}}])
    stale = server._SessionGraph(graph._path)
    stale.load()
    graph.record_activity([{'id': 'wake', 'type': 'wake', 'receiver': 'session', 'at': 100,
                            'tokens': {'input': 10, 'output': 20}}])
    stale.add_edge('parent', 'child')
    stale.save()
    reopened = server._SessionGraph(graph._path)
    reopened.load()
    assert reopened.latest_activity('session')['tokens']['output'] == 20


def test_activity_http_rejects_bad_ranges_before_reading_sessions(monkeypatch):
    import threading
    import urllib.error
    import urllib.request
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid queries must not read transcripts')
    monkeypatch.setattr(activity, 'ticket_activity', forbidden)
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        for query in ('ref=invalid', 'ref=TASK-1&after=nan', 'ref=TASK-1&before=inf',
                      'ref=TASK-1&after=100&before=50'):
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{httpd.server_port}/api/idea-tickets/activity?{query}', timeout=5)
                raise AssertionError('Expected invalid query to be rejected')
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
