"""Progressive sidebar selection preserves pins/ancestry and full-list contracts."""
import json
import threading
import urllib.request

import server
from ccc_server import perf_events


def _isolate(monkeypatch):
    monkeypatch.setattr(server._session_graph, 'parent_snapshot', lambda: {})
    monkeypatch.setattr(server._idea_tickets, 'idea_ticket_rows_map', lambda: {})
    monkeypatch.setattr(server, '_load_usage_limit_resumes', lambda: {})
    # Observe the real selected rows without metadata writes or title scans.
    monkeypatch.setattr(server, '_archive_list_project_rows',
                        lambda rows, **kw: [dict(r) for r in kw['_inputs'][3]])
    monkeypatch.setattr(server, '_queue_health_stats', lambda *a, **kw: {})
    monkeypatch.setattr(server, '_trace_row_flicker', lambda *a, **kw: None)


def test_latest_500_keep_pin_ancestor_cycles_and_source_unchanged(monkeypatch):
    _isolate(monkeypatch)
    rows = [{'session_id': str(i), 'mtime': i} for i in range(600)]
    rows[599]['parent_session_id'] = '1'
    rows[599]['continued_from_session_id'] = '3'
    rows[3]['hermes_continued_from'] = '4'
    rows[1]['parent_session_id'] = '2'
    rows[2]['parent_session_id'] = '1'
    rows[0]['pinned'] = True
    original = json.dumps(rows)
    p = server._archive_list_initial_payload(rows)
    ids = {r['session_id'] for r in p['conversations']}
    assert ids == {str(i) for i in range(100, 600)} | {'0', '1', '2', '3', '4'}
    assert p['count'] == 505 and p['selected_count'] == 600
    assert p['complete'] is False and p['initial'] is True
    assert 'delta_token' not in p
    assert json.dumps(rows) == original


def test_window_parent_context_and_complete_small_selection(monkeypatch):
    _isolate(monkeypatch)
    now = 1000000
    rows = [{'session_id': 'old', 'mtime': 1},
            {'session_id': 'new', 'mtime': now, 'parent_session_id': 'old'},
            {'session_id': 'outside', 'mtime': 2}]
    p = server._archive_list_initial_payload(rows, window='8h', now=now)
    assert {r['session_id'] for r in p['conversations']} == {'new', 'old'}
    assert p['complete'] is True and p['selected_count'] == 2
    assert server._archive_list_initial_payload([], now=now)['complete'] is True


def test_partial_http_never_reads_full_disk_or_creates_delta(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setattr(server, '_archive_list_source_rows_cached',
                        lambda *a, **kw: ([{'session_id': str(i), 'mtime': i} for i in range(601)], True, 1))
    def forbidden(*a, **kw):
        raise AssertionError('partial request entered complete-list cache')
    monkeypatch.setattr(server, '_archive_list_disk_candidate', forbidden)
    monkeypatch.setattr(server, '_archive_list_delta_state', forbidden)
    diagnostics = []
    recorded = threading.Event()
    def record(kind, ms, detail):
        diagnostics.append((kind, ms, detail))
        recorded.set()
    monkeypatch.setattr(server, 'record_diag', record)
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f'http://127.0.0.1:{httpd.server_port}/api/conversations/list?initial=1&since=old'
        with urllib.request.urlopen(url, timeout=5) as response:
            p = json.load(response)
        assert p['count'] == 500 and p['selected_count'] == 601
        assert p['complete'] is False and 'delta_token' not in p
        assert recorded.wait(2)
        kind, elapsed, detail = diagnostics[-1]
        assert kind == 'list_server' and elapsed >= 0
        assert detail['initial'] is True
        for phase in ('source_ms', 'payload_ms', 'send_ms'):
            assert 0 <= detail[phase] <= elapsed + 1
        assert detail['projection']['selection']['cpu_ms'] >= 0
        assert isinstance(detail['src'], dict)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_initial_and_full_metrics_do_not_reclassify_existing_gate(monkeypatch):
    seen = []
    monkeypatch.setattr(perf_events, 'record_diag', lambda *args: seen.append(args))
    for kind in ('archive_initial_load', 'archive_full_load'):
        assert perf_events.record_event(kind, 123, detail={'rows': 500})['ok']
    assert [r[0] for r in seen] == ['archive_initial_load', 'archive_full_load']
    assert 'archive_load' in perf_events._VALID_KINDS
    assert not set(perf_events._CLIENT_DIAG_KINDS) & set(perf_events._VALID_KINDS)
