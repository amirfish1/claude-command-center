"""Compaction must execute a native command, never an ordinary model prompt."""
import json
import sys

import server  # noqa: F401
from ccc_server import engines
from ccc_server import antigravity_compact as native


def test_resume_intercepts_compact_before_any_text_delivery(monkeypatch):
    calls = []
    monkeypatch.setattr(native, 'compact_session', lambda sid: calls.append(sid) or {'ok': True})
    assert engines.resume_session_antigravity('test-session', '/compact') == {'ok': True}
    assert calls == ['test-session']


def test_model_claim_is_not_a_checkpoint(tmp_path):
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text(json.dumps({'type': 'PLANNER_RESPONSE', 'status': 'DONE',
                                      'content': 'Context compacted.'}) + '\n')
    assert native.checkpoint_count(transcript) == 0
    with transcript.open('a') as fh:
        fh.write(json.dumps({'type': 'CHECKPOINT', 'status': 'RUNNING',
                             'content': '# Resuming from a compaction'}) + '\n')
    assert native.checkpoint_count(transcript) == 0
    with transcript.open('a') as fh:
        fh.write(json.dumps({'type': 'CHECKPOINT', 'status': 'DONE',
                             'content': '# Resuming from a compaction'}) + '\n')
    assert native.checkpoint_count(transcript) == 1


def test_busy_stream_queues_without_retiring_or_running_terminal(monkeypatch):
    monkeypatch.setattr(native.stream, 'state', lambda sid: {'busy': True, 'pid': 123})
    writes = []
    monkeypatch.setattr(native._core, '_apply_pending_input_operations',
                        lambda sid, ops: writes.append(ops) or {'ok': True})
    monkeypatch.setattr(native.stream, 'retire', lambda sid: (_ for _ in ()).throw(AssertionError('busy retire')))
    result = native.compact_session('test-session')
    assert result['queued'] is True
    assert writes[0][0]['value'] == '/compact'


def test_hidden_terminal_waits_for_prompt_and_real_checkpoint(tmp_path):
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text('')
    # Exercise real PTY I/O and cleanup with a deterministic local child.
    script = (
        "import sys,json,os; print('> \\nModel: Example',flush=True); cmd=os.read(0,4096); "
        "assert cmd==b'\\x15\\x1b[200~/compact\\x1b[201~\\r'; "
        f"f=open({str(transcript)!r},'a'); "
        "f.write(json.dumps({'type':'CHECKPOINT','status':'DONE',"
        "'content':'# Resuming from a compaction'})+'\\n'); f.close(); input()"
    )
    result = native.run_terminal([sys.executable, '-u', '-c', script],
                                 str(tmp_path), None, transcript, timeout=5)
    assert result['ok'] is True
    assert result['status'] == 'completed'
    assert native.checkpoint_count(transcript) == 1


def test_other_input_cannot_race_hidden_compaction(monkeypatch):
    native._ACTIVE.add('test-session')
    writes = []
    try:
        monkeypatch.setattr(native._core, '_apply_pending_input_operations',
                            lambda sid, ops: writes.append(ops) or {'ok': True})
        result = engines.resume_session_antigravity('test-session', 'next request')
        assert result['queued'] is True
        assert writes[0][0]['value'] == 'next request'
    finally:
        native._ACTIVE.discard('test-session')


def test_app_owned_conversation_never_opens_competing_cli(monkeypatch, tmp_path):
    cli = tmp_path / 'missing-cli.db'
    app = tmp_path / 'app.db'
    app.touch()
    monkeypatch.setattr(native._core, '_antigravity_cli_conversation_path', lambda sid: cli)
    monkeypatch.setattr(native._core, '_antigravity_app_conversation_path', lambda sid: app)
    monkeypatch.setattr(native.stream, 'state', lambda sid: (_ for _ in ()).throw(AssertionError('must guard app first')))
    result = native.compact_session('app-session')
    assert result['ok'] is False
    assert result['code'] == 'compact_needs_manual'
    assert 'Antigravity app' in result['error']


def test_queue_pump_preserves_fifo_while_native_compaction_owns_session(monkeypatch):
    from ccc_server import pending_inputs
    native._ACTIVE.add('test-session')
    try:
        monkeypatch.setattr(native._core, '_is_antigravity_session', lambda sid: True)
        monkeypatch.setattr(native.stream, 'state', lambda sid: None)
        monkeypatch.setattr(native._core, '_antigravity_live_resume_pid', lambda sid: None)
        monkeypatch.setattr(native._core, '_is_devin_cli_session', lambda sid: False)
        monkeypatch.setattr(native._core, '_is_codex_session', lambda sid: False)
        assert pending_inputs._resume_queue_engine_busy('test-session') is True
    finally:
        native._ACTIVE.discard('test-session')
