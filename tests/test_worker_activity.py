"""Usage receipts, rather than transcript writes, drive the compact header."""
import json
import threading
from datetime import datetime, timezone

from ccc_server import worker_activity as activity


def write(path, *records):
    path.write_text(''.join(json.dumps(r) + '\n' for r in records))
    return path


def token(ts, total=100, usage=None):
    return {"type": "event_msg", "timestamp": ts, "payload": {"type": "token_count", "info": {
        "total_token_usage": {"input_tokens": total},
        "last_token_usage": usage if usage is not None else {"input_tokens": 10}}}}


def test_codex_ignores_tools_zero_usage_and_repeated_receipts(tmp_path):
    path = write(tmp_path / 'rollout.jsonl', token('2026-01-01T10:00:00Z'),
                 token('2026-01-01T10:04:00Z'), token('2026-01-01T10:05:00Z', 200, {"input_tokens": 0}),
                 {"type": "event_msg", "timestamp": '2026-01-01T10:06:00Z', "payload": {"type": "tool_result"}})
    assert activity.read_tail(path)['last_token_at'] == datetime(2026, 1, 1, 10, tzinfo=timezone.utc).timestamp()


def test_claude_deduplicates_content_blocks_and_ignores_synthetic(tmp_path):
    def message(ts, mid, model='test-model'):
        return {"type": "assistant", "timestamp": ts, "message": {
            "id": mid, "model": model, "usage": {"output_tokens": 20}}}
    path = write(tmp_path / 'session.jsonl', message('2026-01-01T10:00:00Z', 'a'),
                 message('2026-01-01T10:01:00Z', 'a'),
                 message('2026-01-01T10:02:00Z', 'b', '<synthetic>'))
    assert activity.read_tail(path)['last_token_at'] == datetime(2026, 1, 1, 10, tzinfo=timezone.utc).timestamp()


def test_explicit_plan_and_todo_progress_only(tmp_path):
    path = write(tmp_path / 'plan.jsonl', {"type": "response_item", "payload": {
        "type": "function_call", "name": "functions.update_plan", "arguments": json.dumps({
            "plan": [{"step": "Build", "status": "completed"}, {"step": "Verify", "status": "in_progress"}]})}})
    assert activity.read_tail(path) == {"last_token_at": None, "plan": {"done": 1, "total": 2}}
    write(path, {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "TodoWrite", "input": {
        "todos": [{"content": "Check", "status": "completed"}]}}]}})
    assert activity.read_tail(path)['plan'] == {"done": 1, "total": 1}


def test_tail_is_bounded_and_changed_files_invalidate_cache(tmp_path, monkeypatch):
    path = write(tmp_path / 'large.jsonl', token('2026-01-01T10:00:00Z'))
    with path.open('a') as stream:
        stream.write('x' * 4096 + '\n{bad json\n')
    monkeypatch.setattr(activity, 'TAIL_BYTES', 1024)
    assert activity.read_tail(path)['last_token_at'] is None
    write(path, token('2026-01-01T10:01:00Z', 300))
    assert activity.read_tail(path)['last_token_at'] is not None


def test_only_live_unreleased_unique_workers_and_unknowns_are_preserved(tmp_path):
    path = write(tmp_path / 'session.jsonl', token('2026-01-01T10:00:00Z'))
    worker = {"worker_id": "example-worker", "session_id": "example-session", "alive": True,
              "repo_path": "/example/project", "queue": "EXAMPLE"}
    result = activity.build_snapshot([worker, worker, {**worker, "session_id": "released", "released_at": 'today'},
                                      {**worker, "session_id": "dead", "alive": False},
                                      {**worker, "session_id": "unknown"}],
                                     lambda w: path if w['session_id'] == 'example-session' else None)
    assert len(result['workers']) == 2
    assert result['workers'][0]['last_token_at'] is not None
    assert result['workers'][1]['last_token_at'] is None
    assert result['pending'] is False


def test_refresh_is_single_flight_and_serves_snapshot_without_waiting(monkeypatch):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(activity, '_checked', 0)
    monkeypatch.setattr(activity, '_refreshing', False)
    monkeypatch.setattr(activity, '_snapshot', {"pending": True, "workers": [], "sampled_at": None})
    calls = []
    def load():
        calls.append(True)
        entered.set()
        release.wait(2)
        return []
    original = activity.build_snapshot
    def build(*args):
        result = original(*args)
        finished.set()
        return result
    monkeypatch.setattr(activity, 'build_snapshot', build)
    try:
        assert activity.activity_snapshot(load, lambda w: None)['pending'] is True
        assert entered.wait(2)
        for _ in range(5):
            assert activity.activity_snapshot(load, lambda w: None)['pending'] is True
        assert len(calls) == 1
    finally:
        release.set()
        assert finished.wait(2)
        # Join the owned refresh before monkeypatch restores its globals.
        for thread in threading.enumerate():
            if thread.name == 'worker-activity-refresh':
                thread.join(2)
    assert activity.activity_snapshot(load, lambda w: None)['pending'] is False
    assert len(calls) == 1
