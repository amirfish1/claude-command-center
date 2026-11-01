"""Startup diagnostics must record progress without retiming streamed tokens."""
import importlib
from unittest import mock

server = importlib.import_module('server')


def test_codex_progress_marks_are_first_write_wins(monkeypatch):
    sid = 'test-startup-thread'
    monkeypatch.setattr(server, '_spawn_timeline_sync', lambda: None)
    monkeypatch.setattr(server, '_spawn_timeline_save', mock.Mock())
    monkeypatch.setattr(server, '_save_codex_app_server_state_unlocked', lambda: None)
    server._spawn_timeline_start(sid, engine='codex')
    handler = importlib.import_module('ccc_server.codex')._codex_app_server_handle_notification
    params = {'threadId': sid, 'itemId': 'reasoning', 'delta': 'Working'}
    handler('item/reasoning/summaryTextDelta', params)
    first = dict(server._SPAWN_TIMELINE[sid]['marks'])
    handler('item/reasoning/summaryTextDelta', params)
    assert server._SPAWN_TIMELINE[sid]['marks'] == first
    assert 'codex_first_notification' in first
    assert 'codex_first_reasoning' in first
    handler('item/agentMessage/delta', dict(params, itemId='answer', delta='Ready'))
    assert 'codex_first_text_delta' in server._SPAWN_TIMELINE[sid]['marks']
    assert server._spawn_timeline_save.call_count == 2
    server._SPAWN_TIMELINE.pop(sid, None)


def test_old_worker_retries_only_rejected_timing_keyword(monkeypatch):
    calls = []
    def route(engine, operation, args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            return {"ok": False, "code": "engine_dispatch_failed", "error": "spawn_session_codex() got an unexpected keyword argument 'timeline_t0_epoch_ms'"}
        return {"ok": True, "session_id": "test-thread"}
    monkeypatch.setattr(server, '_control_plane_engine_call', route)
    got = server.spawn_session_codex('Ready', cwd='/tmp', timeline_t0_epoch_ms=123)
    assert got['ok']
    assert calls[0]['timeline_t0_epoch_ms'] == 123
    assert 'timeline_t0_epoch_ms' not in calls[1]
