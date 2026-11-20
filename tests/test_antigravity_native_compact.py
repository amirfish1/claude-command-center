"""Compaction must execute a native command, never an ordinary model prompt."""
import json
import sys
import threading
import time

import pytest
import server
from ccc_server import engines
from ccc_server import antigravity_compact as native


@pytest.mark.parametrize('threshold,should_compact', [(200, True), (250, False)])
def test_resume_honors_saved_auto_compact_threshold(monkeypatch, tmp_path, threshold, should_compact):
    from ccc_server.pending_inputs import _PendingInputEntry

    sid = 'auto-compact-example'
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text(json.dumps({
        'type': 'PLANNER_RESPONSE', 'source': 'MODEL', 'status': 'DONE',
        'content': 'Finished.', 'input_tokens': 125000, 'cache_read_tokens': 75000,
    }) + '\n')
    monkeypatch.delenv('CCC_WORKER_PROCESS', raising=False)
    monkeypatch.setattr(server, '_load_spawn_defaults', lambda: {'auto_compact_k': threshold})
    monkeypatch.setattr(server, '_antigravity_transcript_path', lambda sid: transcript)
    monkeypatch.setattr(server, '_antigravity_app_conversation_path', lambda sid: None)
    monkeypatch.setattr(server, '_resolve_antigravity_bin', lambda: {'available': True})
    monkeypatch.setattr(server, '_get_session_override', lambda sid: None)
    monkeypatch.setattr(server, '_antigravity_command_words', lambda resolved: ['agy'])
    monkeypatch.setattr(server, '_is_pid_alive', lambda pid: False)
    monkeypatch.setattr(server, 'find_session_cwd', lambda sid: str(tmp_path))
    monkeypatch.setattr(engines, '_antigravity_terminal_holder_pid', lambda sid: None)
    monkeypatch.setattr(engines, '_antigravity_live_resume_pid', lambda sid: None)
    monkeypatch.setattr(native.accounts, 'session_account', lambda sid: 'primary')
    monkeypatch.setattr(native.accounts, 'configure_command', lambda cmd, *args: cmd)
    monkeypatch.setattr(native.accounts, 'launch_env', lambda account: {})
    monkeypatch.setattr(native, '_AUTO_ATTEMPTED', {})
    stream_state = {'busy': False, 'pid': 123, 'key': ('', ()), 'turns': 1}
    monkeypatch.setattr(native.stream, 'state', lambda sid: stream_state)
    monkeypatch.setattr(native.stream, 'retire', lambda sid: None)
    sent, writes = [], []
    monkeypatch.setattr(native.stream, 'send', lambda sid, text: sent.append(text) or {'ok': True})
    monkeypatch.setattr(server, '_apply_pending_input_operations',
                        lambda sid, ops: writes.extend(ops) or {'ok': True})
    completed = threading.Event()
    events = []
    monkeypatch.setattr(server, '_log_activity', lambda *args: completed.set())

    def run_terminal(*args):
        events.append('attempted')
        return {'ok': False, 'error': 'Test compaction failure'}

    monkeypatch.setattr(native, 'run_terminal', run_terminal)
    pending = _PendingInputEntry('next request', 'original-delivery')
    result = engines.resume_session_antigravity(sid, pending)
    assert result.get('ok')
    if should_compact:
        assert result['queued']
        assert not sent
        assert writes == [{'field': 'resume', 'action': 'insert_front', 'value': pending}]
        assert writes[0]['value'] is pending
        assert completed.wait(2)
        # A failed attempt cannot keep the same queued send in a compact loop.
        assert events == ['attempted']
        worker = next((thread for thread in threading.enumerate()
                       if thread.name == f'agy-compact-{sid[:8]}'), None)
        if worker is not None:
            worker.join(2)
        assert not native.is_active(sid)
        assert engines.resume_session_antigravity(sid, pending) == {'ok': True}
        assert sent == ['next request']
    else:
        assert sent == ['next request']
        assert not writes


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


@pytest.mark.parametrize('prompt', [
    '> \nModel: Example',
    '> Accept-edits mode: file edits auto-approved\n? for shortcuts   accept-edits',
])
def test_hidden_terminal_waits_for_prompt_and_real_checkpoint(tmp_path, prompt):
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text('')
    # Exercise real PTY I/O and cleanup with a deterministic local child.
    script = (
        f"import sys,json,os,time,termios,signal; print({prompt!r},flush=True); "
        "time.sleep(1.1); termios.tcflush(0,termios.TCIFLUSH); "
        f"print({prompt!r},flush=True); cmd=os.read(0,4096); "
        "assert cmd==b'\\x15\\x1b[200~/compact \\x1b[201~'; "
        "signal.signal(signal.SIGWINCH, lambda *_: print('> /compact ',flush=True)); "
        "assert os.read(0,4096)==b'\\r'; "
        f"f=open({str(transcript)!r},'a'); "
        "f.write(json.dumps({'type':'CHECKPOINT','status':'DONE',"
        "'content':'# Resuming from a compaction'})+'\\n'); f.close(); input()"
    )
    result = native.run_terminal([sys.executable, '-u', '-c', script],
                                 str(tmp_path), None, transcript, timeout=9)
    assert result['ok'] is True
    assert result['status'] == 'completed'
    assert native.checkpoint_count(transcript) == 1


def test_hidden_terminal_yields_at_workspace_trust_prompt(tmp_path):
    transcript = tmp_path / 'transcript.jsonl'
    transcript.touch()
    attempted_input = tmp_path / 'unexpected-input'
    script = (
        'import os; '
        'print("Do you trust the contents of this project?\\r\\n'
        '> Yes, I trust this folder\\r\\n  No, exit", flush=True); '
        f'os.read(0, 4096); open({str(attempted_input)!r}, "w").close()'
    )
    started = time.monotonic()
    result = native.run_terminal([sys.executable, '-u', '-c', script],
                                 str(tmp_path), None, transcript, timeout=10)

    assert result['ok'] is False
    assert result['code'] == 'compact_workspace_trust_required'
    assert time.monotonic() - started < 5
    assert not attempted_input.exists()
    assert native.checkpoint_count(transcript) == 0


def test_send_joins_resume_queue_after_compaction_releases_it(monkeypatch):
    sid = 'auto-compact-example'
    earlier = server._PendingInputEntry('earlier', 'first-id')
    monkeypatch.setattr(server, '_pending_resume_queue', {sid: [earlier]})
    monkeypatch.setattr(server, '_refresh_queued_view_if_file_changed', lambda sid: None)
    writes = []
    monkeypatch.setattr(server, '_apply_pending_input_operations',
                        lambda sid, ops: writes.extend(ops) or {'ok': True})
    monkeypatch.setattr(server, '_resolve_antigravity_bin',
                        lambda: pytest.fail('new send overtook the queue'))

    assert engines.resume_session_antigravity(sid, 'follow-up')['queued'] is True
    assert writes == [{'field': 'resume', 'action': 'append_tail', 'value': 'follow-up'}]


def test_other_input_cannot_race_hidden_compaction(monkeypatch):
    native._ACTIVE.add('test-session')
    writes = []
    try:
        monkeypatch.setattr(native._core, '_apply_pending_input_operations',
                            lambda sid, ops: writes.append(ops) or {'ok': True})
        result = engines.resume_session_antigravity('test-session', 'next request')
        assert result['queued'] is True
        result = engines.resume_session_antigravity('test-session', 'next request', mode='steer')
        assert result['queued_preserved'] is True
        assert writes == [[{'field': 'resume', 'action': 'append_tail', 'value': 'next request'}]]
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
