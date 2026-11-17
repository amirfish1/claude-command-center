"""Model switches preserve turns and deliver once to a replacement process.

The executable fixture speaks the real stream-json transport over a pipe;
it records model, PID and session ID instead of making any provider request.
"""
import concurrent.futures
import json
import os
import signal
import subprocess
import time
from unittest import mock

import pytest
import server
from ccc_server import domestic_providers, engines, free_runtime


SID = "00000000-0000-4000-8000-000000000547"


def eventually(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, "fixture process did not reach the expected boundary"
        time.sleep(0.01)


@pytest.fixture
def delivery(monkeypatch, tmp_path):
    monkeypatch.setenv("CCC_CONTROL_PLANE_ENGINES", "0")
    cli = tmp_path / "fixture-cli"
    cli.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys, time
args = sys.argv
model = args[args.index('--model') + 1] if '--model' in args else 'claude-fable-5-2'
sid = args[args.index('--resume') + 1]
root = pathlib.Path.cwd()
if model == 'claude-sonnet-5-5' and (root / 'old-pid').exists():
    try:
        os.kill(int((root / 'old-pid').read_text()), 0)
        alive = True
    except ProcessLookupError:
        alive = False
    (root / 'replacement.json').write_text(json.dumps({'previous_alive': alive}))
for line in sys.stdin:
    event = json.loads(line)
    text = event['message']['content'][0]['text']
    row = {'pid': os.getpid(), 'model': model, 'sid': sid, 'text': text}
    with (root / 'transcript.jsonl').open('a') as f:
        f.write(json.dumps(row) + '\\n')
    print(json.dumps({'type': 'assistant', 'message': {'content': text}}), flush=True)
    if text in ('hold', 'limit-hold'):
        while not (root / 'finish').exists():
            time.sleep(.01)
    limited = text in ('limit', 'limit-hold')
    print(json.dumps({'type': 'result', 'is_error': limited or text == 'error',
                      'result': "You've hit your Fable limit" if limited else model,
                      'user_message_uuid': event['uuid']}), flush=True)
    print(json.dumps({'type': 'command_lifecycle', 'state': 'completed', 'command_uuid': event['uuid']}), flush=True)
''')
    cli.chmod(0o755)
    override = {"model": "sonnet-5-5"}
    entries = []
    monkeypatch.setattr(server, "_spawned_sessions", entries)
    monkeypatch.setattr(server, "_claude_subagent_parent_session_id", lambda sid: None)
    monkeypatch.setattr(server, "_get_session_override", lambda sid: override)
    monkeypatch.setattr(server, "_free_failover_session_env", lambda sid: None)
    monkeypatch.setattr(free_runtime, "session_runtime", lambda sid: "")
    monkeypatch.setattr(server, "_resolve_cwd_context", lambda cwd: {"cwd": str(tmp_path), "repo_path": str(tmp_path)})
    monkeypatch.setattr(server, "repo_from_session", lambda sid: {"cwd": str(tmp_path), "repo_path": str(tmp_path)})
    monkeypatch.setattr(server, "_ensure_session_jsonl_for_cwd", lambda *a: {"ok": True})
    monkeypatch.setattr(server, "repo_log_dir", lambda *a: tmp_path)
    monkeypatch.setattr(server, "_resolve_claude_bin", lambda: {"available": True, "bin": str(cli)})
    monkeypatch.setattr(server, "_claude_session_state_args", lambda: [])
    monkeypatch.setattr(server, "_claude_peer_inbound_args", lambda: [])
    monkeypatch.setattr(server, "_make_stdin_fifo", lambda *a: (None, None))
    for name in ("_record_spawn_to_registry", "_remove_spawn_from_registry",
                 "_update_spawn_input_state_in_registry", "_update_spawn_transcript_watermark",
                 "_resume_ledger_append", "_log_activity", "_record_kill_event"):
        monkeypatch.setattr(server, name, mock.Mock())
    monkeypatch.setattr(server, "_spawn_entry_active_tool_child", lambda entry: None)
    monkeypatch.setattr(server, "_ensure_headless_staleness_watcher_started", mock.Mock())
    monkeypatch.setattr(server, "find_session_cwd", lambda sid: str(tmp_path))
    monkeypatch.setattr(server, "session_live_status", lambda *a: {"live": False, "tty": None})
    monkeypatch.setattr(server, "_detect_session_engine", lambda sid: "claude")
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", lambda sid: next((e for e in entries if e['proc'].poll() is None), None))
    monkeypatch.setattr(server, "_terminal_input_queue_has_pending", lambda sid: False)
    monkeypatch.setattr(server, "_session_has_live_terminal", lambda *a, **kw: False)
    monkeypatch.setattr(server, "_inject_budget_check", lambda *a: None)
    monkeypatch.setattr(server, "_ask_question_blocking_inject", lambda *a: False)
    monkeypatch.setattr(server, "_try_uds_peer_delivery", mock.Mock(return_value=None))
    monkeypatch.setattr(server, "_queue_terminal_input", mock.Mock(return_value={"ok": True, "queued": True}))
    children = []

    def start(model="claude-fable-5-2", legacy=False):
        log = tmp_path / f"old-{len(children)}.log"
        fh = log.open('w')
        cmd = [str(cli), '--resume', SID]
        if model:
            cmd += ['--model', model]
        proc = subprocess.Popen(cmd, cwd=tmp_path, stdin=subprocess.PIPE, stdout=fh, start_new_session=True)
        children.append(proc)
        entry = {"pid": proc.pid, "proc": proc, "log": str(log), "log_fh": fh,
                 "resumed_sid": SID, "cwd": str(tmp_path), "engine": "claude"}
        if not legacy:
            entry["model"] = model
        entries.append(entry)
        return entry

    def rows():
        transcript = tmp_path / 'transcript.jsonl'
        return [json.loads(line) for line in transcript.read_text().splitlines()] if transcript.exists() else []

    yield start, entries, override, rows, tmp_path
    for entry in entries:
        if isinstance(entry.get("proc"), subprocess.Popen) and entry["proc"] not in children:
            children.append(entry["proc"])
    for proc in children:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        if proc.stdin:
            proc.stdin.close()
    for entry in entries:
        engines._cleanup_finished_entry(entry)


@pytest.mark.parametrize("legacy", [False, True, 'reattached'])
def test_idle_switch_waits_for_exit_and_delivers_once_with_same_session(delivery, legacy):
    start, entries, _, rows, root = delivery
    old = start(legacy=legacy)
    if legacy == 'reattached':
        old['proc'] = server._ReattachedProc(old['pid'])
    (root / 'old-pid').write_text(str(old['pid']))
    result = server.resume_session_headless(SID, 'next')
    assert result['ok'] and result['pid'] != old['pid']
    assert old['proc'].poll() is not None
    eventually(lambda: len(rows()) == 1)
    assert rows() == [{'pid': result['pid'], 'model': 'claude-sonnet-5-5', 'sid': SID, 'text': 'next'}]
    assert entries[0]['model'] == 'sonnet-5-5'
    assert json.loads((root / 'replacement.json').read_text()) == {'previous_alive': False}
    assert server._record_spawn_to_registry.call_args.kwargs['model'] == 'sonnet-5-5'


def test_pick_mid_turn_keeps_process_then_submit_queues_and_drains_once(delivery):
    start, entries, override, rows, root = delivery
    old = start()
    assert server._write_stream_json_user_message(old, 'hold')
    eventually(lambda: len(rows()) == 1)
    assert server._headless_turn_in_progress(old)
    result = server._set_session_model_headless_local(SID, override['model'], False, '', False)
    assert result['applied'] == 'queued'
    assert old['proc'].poll() is None and not old.get('retire_when_idle')
    submitted = server._inject_text_into_session_router(SID, 'after hold', mode='steer', force_headless=True)
    assert submitted['queued'] and submitted['model_change_pending']
    server._queue_terminal_input.assert_called_once()
    server._try_uds_peer_delivery.assert_not_called()
    assert len(rows()) == 1 and old['proc'].poll() is None
    (root / 'finish').touch()
    eventually(lambda: not server._headless_turn_in_progress(old))
    drained = server._inject_text_into_session_router(SID, 'after hold', _from_terminal_queue=True)
    assert drained['ok'] and not drained.get('queued')
    eventually(lambda: len(rows()) == 2)
    assert [r['text'] for r in rows()] == ['hold', 'after hold']
    assert rows()[0]['model'] == 'claude-fable-5-2'
    assert rows()[1]['model'] == 'claude-sonnet-5-5'
    assert all(r['sid'] == SID for r in rows())
    assert old['proc'].poll() is not None


def test_concurrent_submissions_create_only_one_replacement(delivery):
    start, entries, _, rows, _ = delivery
    old = start()
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda text: server.resume_session_headless(SID, text), ['one', 'two']))
    assert all(r['ok'] for r in results)
    assert len({r['pid'] for r in results}) == 1
    assert old['proc'].poll() is not None
    eventually(lambda: len(rows()) == 2)
    assert {r['text'] for r in rows()} == {'one', 'two'}
    assert len(entries) == 1


def test_warm_synchronous_ask_also_uses_new_model(delivery):
    start, _, _, rows, _ = delivery
    old = start()
    result = server.ask_session_and_wait(SID, 'ask', timeout_ms=3000)
    assert result['ok'] and result['text'] == 'claude-sonnet-5-5'
    assert old['proc'].poll() is not None
    assert len(rows()) == 1 and rows()[0]['model'] == 'claude-sonnet-5-5'


def test_failed_exit_keeps_process_tracked_and_never_delivers(delivery):
    start, entries, _, rows, _ = delivery
    old = start()
    with mock.patch.object(old['proc'], 'wait', side_effect=subprocess.TimeoutExpired('fixture', 2)), \
         mock.patch.object(engines.os, 'killpg'):
        result = server.resume_session_headless(SID, 'must wait')
    assert result['code'] == 'claude_model_switch_pending'
    assert entries == [old] and not rows()
    assert old['proc'].poll() is None


def test_active_tool_child_defers_even_after_turn_log_result(delivery, monkeypatch):
    start, entries, _, rows, _ = delivery
    old = start()
    monkeypatch.setattr(server, '_spawn_entry_active_tool_child', lambda e: {'pid': 123})
    result = server.resume_session_headless(SID, 'must wait')
    assert result['code'] == 'claude_model_switch_pending'
    assert entries == [old] and not rows() and old['proc'].poll() is None


def test_same_normalized_model_reuses_process(delivery):
    start, entries, _, rows, _ = delivery
    old = start(model='claude-sonnet-5-5')
    result = server.resume_session_headless(SID, 'same model')
    assert result['ok'] and result['reused'] and result['pid'] == old['pid']
    eventually(lambda: len(rows()) == 1)
    assert entries == [old]


def test_recovered_default_launch_without_model_flag_switches_before_fifo_write(delivery):
    start, entries, _, rows, _ = delivery
    old = start(model='', legacy=True)
    old['session_id'] = old.pop('resumed_sid')
    old['proc'] = server._ReattachedProc(old['pid'])
    result = server._inject_text_into_session_router(SID, 'hi', force_headless=True)
    assert result['ok'] and old['proc'].poll() is not None
    eventually(lambda: len(rows()) == 1)
    assert rows()[0]['model'] == 'claude-sonnet-5-5'
    assert rows()[0]['sid'] == SID
    assert len(entries) == 1
    retire = next(c for c in server._resume_ledger_append.call_args_list if c.args == ('retire',))
    assert retire.kwargs['sid'] == SID and retire.kwargs['reason'] == 'model-switch'


@pytest.mark.parametrize('busy', [False, True])
def test_queued_choice_retires_only_after_limit_boundary_and_never_auto_sends(delivery, busy):
    start, entries, override, rows, root = delivery
    old = start()
    assert server._write_stream_json_user_message(old, 'limit-hold' if busy else 'limit')
    eventually(lambda: len(rows()) == 1)
    if not busy:
        eventually(lambda: not server._headless_turn_in_progress(old))
    picked = server._set_session_model_headless_local(SID, override['model'], False, '', False)
    assert picked['ok'] and picked['applied'] == 'queued'
    if busy:
        assert old['proc'].poll() is None
        assert old['_model_switch_after_limit']
        assert not server._retire_limited_model_for_session(SID)
        (root / 'finish').touch()
        eventually(lambda: not server._headless_turn_in_progress(old))
        assert server._retire_limited_model_for_session(SID)
    assert old['proc'].poll() is not None and not entries
    assert len(rows()) == 1  # exhaustion never generates a retry/continue
    assert server._resume_ledger_append.call_args.kwargs['reason'] == 'model-switch'
    result = server.resume_session_headless(SID, 'hi')
    assert result['ok']
    eventually(lambda: len(rows()) == 2)
    assert rows()[1]['model'] == 'claude-sonnet-5-5'


@pytest.mark.parametrize('text', ['done', 'error'])
def test_non_limit_result_does_not_retire_on_pick(delivery, text):
    start, entries, override, rows, _ = delivery
    old = start()
    assert server._write_stream_json_user_message(old, text)
    eventually(lambda: len(rows()) == 1 and not server._headless_turn_in_progress(old))
    server._set_session_model_headless_local(SID, override['model'], False, '', False)
    assert entries == [old] and old['proc'].poll() is None
    assert not server._retire_limited_model_for_session(SID)


@pytest.mark.parametrize('guard', ['same-model', 'active-tool', 'invalid-preset'])
def test_exhaustion_retirement_preserves_model_runtime_guards(delivery, monkeypatch, guard):
    start, entries, override, rows, _ = delivery
    old = start()
    assert server._write_stream_json_user_message(old, 'limit')
    eventually(lambda: len(rows()) == 1 and not server._headless_turn_in_progress(old))
    if guard == 'same-model':
        override['model'] = old['model']
    elif guard == 'active-tool':
        monkeypatch.setattr(server, '_spawn_entry_active_tool_child', lambda e: {'pid': 123})
    else:
        def unavailable(*a):
            raise domestic_providers.DomesticProviderError('Missing test key', 'preset_key_missing')
        monkeypatch.setattr(domestic_providers, 'resolve_spawn', unavailable)
    server._set_session_model_headless_local(SID, override['model'], False, '', False)
    assert not server._retire_limited_model_for_session(SID)
    assert entries == [old] and old['proc'].poll() is None and len(rows()) == 1


def test_legacy_startup_with_no_output_is_not_an_idle_boundary(delivery):
    start, entries, _, rows, _ = delivery
    old = start(legacy=True)
    old['prompt'] = 'previous accepted input'
    result = server.resume_session_headless(SID, 'must wait')
    assert result['code'] == 'claude_model_switch_pending'
    assert entries == [old] and not rows() and old['proc'].poll() is None


def test_invalid_paid_preset_keeps_old_process_and_message(delivery, monkeypatch):
    start, entries, override, rows, _ = delivery
    old = start()
    override['model'] = 'byok/kimi-intl/kimi-k3'
    def missing_key(*a):
        raise domestic_providers.DomesticProviderError('Missing test key', 'preset_key_missing')
    monkeypatch.setattr(domestic_providers, 'resolve_spawn', missing_key)
    result = server.resume_session_headless(SID, 'do not send')
    assert result['code'] == 'preset_key_missing'
    assert entries == [old] and not rows() and old['proc'].poll() is None


def test_paid_preset_switch_uses_new_environment_and_keeps_provider_identity(delivery, monkeypatch):
    start, entries, override, rows, _ = delivery
    old = start(model='kimi-k3')
    old['model'] = 'byok/kimi-cn/kimi-k3'
    override['model'] = 'byok/kimi-intl/kimi-k3'
    monkeypatch.setattr(domestic_providers, 'resolve_spawn', lambda *a: {
        'model': 'kimi-k3', 'env': {'ANTHROPIC_BASE_URL': 'https://provider.example.test/anthropic'},
    })
    result = server.resume_session_headless(SID, 'new region')
    assert result['ok'] and result['pid'] != old['pid']
    eventually(lambda: len(rows()) == 1)
    assert rows()[0]['model'] == 'kimi-k3'
    assert entries[0]['model'] == 'byok/kimi-intl/kimi-k3'
    assert old['proc'].poll() is not None
