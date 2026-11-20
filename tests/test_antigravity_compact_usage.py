"""Post-compaction context must not reuse a pre-compaction measurement."""
import json
import server  # noqa: F401
from ccc_server import antigravity_compact as native
from ccc_server import engines


def test_checkpoint_invalidates_old_context_until_new_measurement(tmp_path, monkeypatch):
    transcript = tmp_path / 'transcript.jsonl'
    checkpoint = {'type': 'CHECKPOINT', 'status': 'DONE', 'step_index': 3,
                  'content': '# Resuming from a compaction', 'input_tokens': 388974,
                  'output_tokens': 5079}
    transcript.write_text(json.dumps(checkpoint) + '\n')
    monkeypatch.setattr(native._core, 'COMMAND_CENTER_STATE_DIR', str(tmp_path))
    monkeypatch.setattr(engines._core, '_antigravity_transcript_path', lambda sid: transcript)
    monkeypatch.setattr(engines._core, '_extract_antigravity_tail_meta', lambda p: {})
    monkeypatch.setattr(engines._core, '_get_session_override', lambda sid: None)
    steps = [{'stepIndex': 1, 'metadata': {'modelUsage': {'inputTokens': 388000, 'outputTokens': 100}}}]
    monkeypatch.setattr(engines, '_antigravity_trajectory_steps', lambda sid: steps)
    usage = engines._extract_antigravity_usage('sample')
    assert usage['latest_input_tokens'] == 0
    assert usage['context_after_compact_pending'] is True
    assert usage['total_input_tokens'] == 388000
    native.save_context_estimate(transcript, 20300)
    usage = engines._extract_antigravity_usage('sample')
    assert usage['latest_input_tokens'] == 20300
    assert usage['context_estimated'] is True
    assert usage['peak_input_tokens'] == 20300
    steps.append({'stepIndex': 4, 'metadata': {'modelUsage': {'inputTokens': 22000}}})
    usage = engines._extract_antigravity_usage('sample')
    assert usage['latest_input_tokens'] == 22000
    assert not usage.get('context_estimated')
    # A new checkpoint must never inherit the previous checkpoint's estimate.
    checkpoint['step_index'] = 5
    with transcript.open('a') as fh:
        fh.write(json.dumps(checkpoint) + '\n')
    assert engines._extract_antigravity_usage('sample')['latest_input_tokens'] == 0


def test_native_context_estimate_is_not_summary_output_tokens():
    assert native.terminal_context_estimate('context 389.0k → ~20.3k (5.0k summary)') == 20300
    assert native.terminal_context_estimate('context 900k → ~1.2M (5k summary)') == 1200000
    assert native.terminal_context_estimate('Compacting conversation 5.0k summary') is None


def test_context_inspection_reads_native_card_without_sending_a_prompt(tmp_path, monkeypatch):
    import sys
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text(json.dumps({'type': 'CHECKPOINT', 'status': 'DONE',
                                     'step_index': 12, 'content': '# Resuming from a compaction'}) + '\n')
    monkeypatch.setattr(native._core, 'COMMAND_CENTER_STATE_DIR', str(tmp_path))
    script = ("import os; print('4 steps summarized · context 389.0k → ~20.3k (5.0k summary)',flush=True); "
              "cmd=os.read(0,4096); assert cmd==b'\\x15/exit\\r'")
    result = native.run_terminal([sys.executable, '-u', '-c', script], str(tmp_path),
                                 None, transcript, timeout=3, context_only=True)
    assert result['context_tokens'] == 20300
    assert native.context_estimate(transcript, native.latest_checkpoint(transcript)) == 20300


def test_native_compact_captures_new_card_instead_of_replayed_card(tmp_path, monkeypatch):
    import sys
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text('')
    monkeypatch.setattr(native._core, 'COMMAND_CENTER_STATE_DIR', str(tmp_path))
    script = (
        "import os,json; print('11 steps summarized · context 900k → ~800k (5k summary)\\n> \\nModel: Example',flush=True); "
        "os.read(0,4096); "
        f"f=open({str(transcript)!r},'a'); "
        "f.write(json.dumps({'type':'CHECKPOINT','status':'DONE','step_index':12,"
        "'content':'# Resuming from a compaction'})+'\\n'); f.close(); "
        "print('4 steps summarized · context 389.0k → ~20.3k (5.0k summary)',flush=True); os.read(0,4096)"
    )
    result = native.run_terminal([sys.executable, '-u', '-c', script], str(tmp_path),
                                 None, transcript, timeout=6)
    assert result['ok'] is True
    assert result['context_tokens'] == 20300
    assert native.context_estimate(transcript, native.latest_checkpoint(transcript)) == 20300
