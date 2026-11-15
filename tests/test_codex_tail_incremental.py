"""Regression test for incremental Codex rollout tail parsing.

`/api/sessions/live-activity` polls `_extract_codex_tail_meta` for every live
session. The function is mtime-cached, but a *live* session appends to its
rollout constantly, so the cache always missed and the WHOLE (multi-MB) file
was re-parsed on every poll — the CPU hog. The parser now resumes from a saved
byte offset and reads only newly-appended lines.

These tests assert:
1. Incremental parsing (poll after each append) yields the SAME meta as a
   single full parse of the final file — semantic equivalence.
2. A second poll reads only the appended bytes (resume offset advances; the
   read is bounded by what was appended, not the whole file).
3. A partially-written trailing line (no newline yet) is not consumed until
   it is completed.
"""
import json

import pytest

import server


def _ev(d: dict) -> str:
    return json.dumps(d) + "\n"


def _session_meta(cwd="/Users/x/dev/proj", model="gpt-5"):
    return _ev({"type": "session_meta", "payload": {"type": "session_meta", "cwd": cwd, "model": model}})


def _user(msg, ts="2026-06-02T10:00:00Z"):
    return _ev({"type": "event_msg", "timestamp": ts, "payload": {"type": "user_message", "message": msg}})


def _agent(msg, ts="2026-06-02T10:00:01Z"):
    return _ev({"type": "event_msg", "timestamp": ts, "payload": {"type": "agent_message", "message": msg}})


def _call(call_id, name="shell", args=None, ts="2026-06-02T10:00:02Z"):
    return _ev({
        "type": "response_item",
        "timestamp": ts,
        "payload": {"type": "function_call", "name": name,
                    "arguments": json.dumps(args or {"command": ["git", "commit", "-m", "x"]}),
                    "call_id": call_id},
    })


def _call_out(call_id, output="ok", ts="2026-06-02T10:00:03Z"):
    return _ev({
        "type": "response_item",
        "timestamp": ts,
        "payload": {"type": "function_call_output", "call_id": call_id, "output": output},
    })


# A realistic multi-turn rollout, as a list of already-newline-terminated lines.
SCRIPT = [
    _session_meta(),
    _user("first question about the build"),
    _agent("looking into it"),
    _call("c1"),
    _call_out("c1"),
    _agent("done, opened a PR"),
    _user("second question"),
    _call("c2", name="apply_patch", args={"input": "*** Begin Patch"}),
]


def test_current_message_format_supplies_sidebar_metadata(tmp_path):
    p = tmp_path / "messages.jsonl"
    def message(role, text, phase=None):
        return _ev({"type": "response_item", "timestamp": "2026-06-02T10:00:01Z",
                    "payload": {"type": "message", "role": role, "phase": phase,
                                "content": [{"type": "output_text" if role == "assistant" else "input_text",
                                             "text": text}]}})
    p.write_text(_session_meta()
                 + message("user", "# AGENTS.md instructions for /example/project\nLocal rules")
                 + message("user", "# AGENTS.md instructions\nLocal rules")
                 + message("user", "<environment_context>Local context</environment_context>")
                 + message("user", "Check sidebar metadata"))
    meta = server._extract_codex_tail_meta(p)
    assert meta["first_message"] == "Check sidebar metadata"
    assert meta["last_prompt"] == "Check sidebar metadata"
    assert meta["last_user_ts"] > 0
    assert meta["last_event_type"] == "user"
    with p.open("a") as f:
        f.write(message("assistant", "Checking the parser", "commentary"))
    assert server._extract_codex_tail_meta(p)["last_assistant_text"] == "Checking the parser"
    final = ("Fixed.\n<session-state>\nDID: Fixed sidebar metadata\n"
             "INSIGHT: Message events changed\nNEXT_STEP_USER: Reload the dashboard\n</session-state>")
    with p.open("a") as f:
        f.write(message("assistant", final, "final_answer"))
        f.write(_ev({"type": "event_msg", "payload": {"type": "task_complete"}}))
    meta = server._extract_codex_tail_meta(p)
    assert meta["last_event_type"] == "result"
    assert server._parse_session_state(meta["last_assistant_text"])["next_step_user"] == "Reload the dashboard"
    with p.open("a") as f:
        f.write(message("developer", "Internal instructions"))
    assert server._extract_codex_tail_meta(p)["last_event_type"] == "result"


def test_task_complete_can_supply_final_reply(tmp_path):
    p = tmp_path / "completed.jsonl"
    p.write_text(_agent("Still checking") + _ev({"type": "event_msg", "payload": {
        "type": "task_complete", "last_agent_message": "Final result"}}))
    assert server._extract_codex_tail_meta(p)["last_assistant_text"] == "Final result"


@pytest.fixture(autouse=True)
def _clear_caches():
    with server._conv_meta_cache_lock:
        server._conv_meta_cache.clear()
        server._codex_tail_resume.clear()
    yield
    with server._conv_meta_cache_lock:
        server._conv_meta_cache.clear()
        server._codex_tail_resume.clear()


def _meta_sans_volatile(meta: dict) -> dict:
    """Drop fields that legitimately depend on file mtime / wall-clock so the
    full-vs-incremental comparison is apples-to-apples."""
    out = dict(meta)
    out.pop("mtime", None)
    out.pop("_tail_checkpoint", None)  # private file/cursor identity, not conversation metadata
    out.pop("pending_tool_ts", None)  # falls back to mtime when no event ts
    return out


def test_incremental_equals_full_parse(tmp_path):
    from pathlib import Path

    # Full parse: write the entire script at once, parse once.
    full_path = tmp_path / "full.jsonl"
    full_path.write_text("".join(SCRIPT))
    full_meta = server._extract_codex_tail_meta(Path(full_path))

    # Incremental: append one line at a time, parsing after each append.
    with server._conv_meta_cache_lock:
        server._conv_meta_cache.clear()
        server._codex_tail_resume.clear()
    incr_path = tmp_path / "incr.jsonl"
    incr_meta = None
    with open(incr_path, "w") as fh:
        for line in SCRIPT:
            fh.write(line)
            fh.flush()
            incr_meta = server._extract_codex_tail_meta(Path(incr_path))

    assert _meta_sans_volatile(incr_meta) == _meta_sans_volatile(full_meta)
    # Sanity: the script's signals actually landed (so the equivalence above
    # is over a non-trivial meta, not two empty dicts).
    assert full_meta["first_message"] == "first question about the build"
    assert full_meta["last_prompt"] == "second question"
    assert full_meta["has_edit"] is True            # the apply_patch call
    assert full_meta["pending_tool"]                # last call left one pending


def test_second_poll_reads_only_appended_bytes(tmp_path):
    from pathlib import Path

    p = tmp_path / "r.jsonl"
    head = "".join(SCRIPT[:4])
    p.write_text(head)
    server._extract_codex_tail_meta(Path(p))
    off1 = server._codex_tail_resume[str(p)]["offset"]
    assert off1 == len(head.encode())  # consumed exactly the head

    # Append the rest; the next poll must resume from off1, not byte 0.
    tail = "".join(SCRIPT[4:])
    with open(p, "a") as fh:
        fh.write(tail)
    server._extract_codex_tail_meta(Path(p))
    off2 = server._codex_tail_resume[str(p)]["offset"]
    assert off2 == len(head.encode()) + len(tail.encode())
    assert off2 > off1


def test_partial_trailing_line_not_consumed(tmp_path):
    from pathlib import Path

    p = tmp_path / "partial.jsonl"
    p.write_text(_session_meta() + _user("complete line"))
    server._extract_codex_tail_meta(Path(p))
    off_complete = server._codex_tail_resume[str(p)]["offset"]

    # Write a line WITHOUT a trailing newline (writer mid-append).
    with open(p, "a") as fh:
        fh.write('{"type": "event_msg", "payload": {"type": "user_message", "message": "half')
    meta = server._extract_codex_tail_meta(Path(p))
    # Offset must not advance into the partial line, and the half-written
    # message must not appear as the last prompt.
    assert server._codex_tail_resume[str(p)]["offset"] == off_complete
    assert meta["last_prompt"] == "complete line"

    # Complete the line; now it should be consumed.
    with open(p, "a") as fh:
        fh.write(' message"}}\n')
    meta2 = server._extract_codex_tail_meta(Path(p))
    assert server._codex_tail_resume[str(p)]["offset"] > off_complete
    assert meta2["last_prompt"] == "half message"


def test_disk_reload_reads_only_appended_records_and_retains_pending_tool(tmp_path, monkeypatch):
    """A fresh archive worker must finish an old call without rereading history."""
    import builtins
    import copy
    from pathlib import Path
    p = tmp_path / 'persisted.jsonl'
    monkeypatch.setattr(server, '_CONV_META_CACHE_FILE', tmp_path / 'metadata.json')
    p.write_text(''.join(SCRIPT[:4]) + (_agent('Padding history ' * 100) * 1000)
                 + _call('pr-call', name='exec_command', args={'cmd': 'gh pr create'}))
    server._extract_codex_tail_meta(p)
    server._save_conv_meta_cache()
    server._conv_meta_cache.clear()
    server._codex_tail_resume.clear()
    server._load_conv_meta_cache()
    tail = _call_out('pr-call', 'https://github.com/example/project/pull/42') + ''.join(SCRIPT[4:])
    with p.open('a') as out:
        out.write(tail)
    reads = []
    original = builtins.open
    class Tracked:
        def __init__(self, source): self.source = source
        def __getattr__(self, name): return getattr(self.source, name)
        def __enter__(self): self.source.__enter__(); return self
        def __exit__(self, *args): return self.source.__exit__(*args)
        def read(self, *args):
            data = self.source.read(*args); reads.append(len(data)); return data
        def readline(self, *args):
            data = self.source.readline(*args); reads.append(len(data)); return data
    def tracked(candidate, *args, **kwargs):
        source = original(candidate, *args, **kwargs)
        return Tracked(source) if Path(candidate) == p and args and args[0] == 'rb' else source
    with monkeypatch.context() as context:
        context.setattr(builtins, 'open', tracked)
        resumed = copy.deepcopy(server._extract_codex_tail_meta(p))
    assert sum(reads) <= len(tail.encode()) + 16384, 'restored cursor reread historical rollout bytes'
    server._conv_meta_cache.clear(); server._codex_tail_resume.clear()
    full = server._extract_codex_tail_meta(p)
    assert _meta_sans_volatile(resumed) == _meta_sans_volatile(full)
    assert resumed['pending_tool'] and resumed['has_edit']
    assert resumed['tail_pr_number'] == 42, 'the pending call must survive worker exit'


@pytest.mark.parametrize('mutation', ['truncate', 'replace', 'rewrite-head', 'rewrite-tail', 'invalid-cursor'])
def test_saved_cursor_falls_back_after_invalid_history(tmp_path, monkeypatch, mutation):
    import copy
    p = tmp_path / 'changed.jsonl'
    monkeypatch.setattr(server, '_CONV_META_CACHE_FILE', tmp_path / 'metadata.json')
    p.write_text(''.join(SCRIPT))
    server._extract_codex_tail_meta(p)
    server._save_conv_meta_cache()
    server._conv_meta_cache.clear(); server._codex_tail_resume.clear()
    server._load_conv_meta_cache()
    if mutation == 'truncate':
        p.write_text(_user('New short history'))
    elif mutation == 'replace':
        replacement = tmp_path / 'new.jsonl'
        replacement.write_text(_user('Replacement') + _agent('New ' * 1000))
        replacement.replace(p)
    elif mutation == 'rewrite-head':
        p.write_text(_session_meta(cwd='/example/replaced') + ''.join(SCRIPT[1:]) + _agent('More data'))
    elif mutation == 'rewrite-tail':
        p.write_text(''.join(SCRIPT[:-1]) + _user('A replacement ending ' * 100))
    else:
        server._conv_meta_cache[str(p)]['_tail_checkpoint']['offset'] = -10
        with p.open('a') as out: out.write(_agent('Fresh append'))
    resumed = copy.deepcopy(server._extract_codex_tail_meta(p))
    server._conv_meta_cache.clear(); server._codex_tail_resume.clear()
    full = server._extract_codex_tail_meta(p)
    assert _meta_sans_volatile(resumed) == _meta_sans_volatile(full)


def test_saved_cursor_keeps_partial_record_unconsumed_across_reload(tmp_path, monkeypatch):
    p = tmp_path / 'partial-reload.jsonl'
    monkeypatch.setattr(server, '_CONV_META_CACHE_FILE', tmp_path / 'metadata.json')
    prefix = _session_meta() + _user('Complete prompt')
    p.write_text(prefix + '{"type":"event_msg","payload":{"type":"user_message","message":"Part')
    server._extract_codex_tail_meta(p)
    server._save_conv_meta_cache()
    server._conv_meta_cache.clear(); server._codex_tail_resume.clear()
    server._load_conv_meta_cache()
    with p.open('a') as out: out.write('ial prompt"}}\n')
    assert server._extract_codex_tail_meta(p)['last_prompt'] == 'Partial prompt'


def test_saved_cursor_preserves_usage_delta_and_counter_resets(tmp_path, monkeypatch):
    import copy
    p = tmp_path / 'usage-reload.jsonl'
    monkeypatch.setattr(server, '_CONV_META_CACHE_FILE', tmp_path / 'metadata.json')
    def usage(i, cached, output):
        totals = {'input_tokens': i, 'cached_input_tokens': cached,
                  'output_tokens': output, 'total_tokens': i + output}
        return _ev({'type': 'event_msg', 'timestamp': '2026-06-02T10:00:04Z', 'payload': {'type': 'token_count',
            'info': {'last_token_usage': totals, 'total_token_usage': totals}}})
    lines = [_session_meta(model='gpt-6-astra') + _user('Usage test'), usage(100000, 80000, 10000),
             usage(200000, 160000, 20000), usage(200000, 160000, 20000),
             usage(10000, 8000, 1000)]
    for line in lines:
        with p.open('a') as out: out.write(line)
        resumed = copy.deepcopy(server._extract_codex_tail_meta(p))
        server._save_conv_meta_cache()
        server._conv_meta_cache.clear(); server._codex_tail_resume.clear()
        server._load_conv_meta_cache()
    server._conv_meta_cache.clear(); server._codex_tail_resume.clear()
    full = server._extract_codex_tail_meta(p)
    assert _meta_sans_volatile(resumed) == _meta_sans_volatile(full)
    assert resumed['cost_usd'] > 0


@pytest.mark.parametrize('terminal', ['turn_aborted', 'task_complete'])
def test_restored_cursor_matches_terminal_receipt_to_started_turn(tmp_path, monkeypatch, terminal):
    """Persisted parser cursors retain the accepted exact-turn terminal logic."""
    import copy
    rollout = tmp_path / 'terminal.jsonl'
    monkeypatch.setattr(server, '_CONV_META_CACHE_FILE', tmp_path / 'metadata.json')
    started = _ev({'type': 'event_msg', 'timestamp': '2026-06-02T10:00:00Z',
                   'payload': {'type': 'task_started', 'turn_id': 'turn-current'}})
    rollout.write_text(_session_meta() + started + _user('continue') + _call('pending'))
    initial = server._extract_codex_tail_meta(rollout)
    assert initial['current_turn_id'] == 'turn-current' and initial['pending_tool']
    server._save_conv_meta_cache()
    server._conv_meta_cache.clear()
    server._codex_tail_resume.clear()
    server._load_conv_meta_cache()
    with rollout.open('a') as stream:
        stream.write(_ev({'type': 'event_msg', 'timestamp': '2026-06-02T10:00:04Z',
                          'payload': {'type': terminal, 'turn_id': 'turn-current',
                                      'reason': 'interrupted'}}))
    restored = copy.deepcopy(server._extract_codex_tail_meta(rollout))
    assert restored['last_event_type'] == 'result'
    assert restored['last_terminal_turn_id'] == 'turn-current'
    assert not restored['current_turn_id'] and not restored['pending_tool']
    server._conv_meta_cache.clear()
    server._codex_tail_resume.clear()
    full = server._extract_codex_tail_meta(rollout)
    assert _meta_sans_volatile(restored) == _meta_sans_volatile(full)
