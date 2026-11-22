"""Incremental tail-parse regression tests for Cursor and Antigravity.

Both transcripts are append-only JSONL, so — like Codex — `_extract_*_tail_meta`
now resumes from a saved byte offset and parses only newly-appended lines
instead of re-reading the whole file on every live-activity poll. (Gemini is a
single JSON document rewritten in full, so it can't use this and is unchanged.)

Asserted per engine:
1. Incremental parsing (poll after each append) == a single full parse.
2. A second poll reads only appended bytes (resume offset advances).
3. A partially-written trailing line is not consumed until completed.
"""
import json
from pathlib import Path

import pytest

import server
from ccc_server import antigravity_stream, engines


# --- Cursor fixtures --------------------------------------------------------

def _cursor_user(msg, ts="2026-06-02T10:00:00Z"):
    return json.dumps({"role": "user", "timestamp": ts, "content": msg}) + "\n"


def _cursor_assistant(text, tool=None, ts="2026-06-02T10:00:01Z"):
    content = [{"type": "text", "text": text}]
    if tool:
        content.append({"type": "tool_use", "name": tool, "input": {"command": "git commit -m x"}})
    return json.dumps({"role": "assistant", "timestamp": ts, "content": content}) + "\n"


CURSOR_SCRIPT = [
    _cursor_user("first cursor question"),
    _cursor_assistant("looking", tool="edit"),
    _cursor_assistant("done"),
    _cursor_user("second cursor question"),
    _cursor_assistant("on it", tool="bash"),
]


# --- Antigravity fixtures ---------------------------------------------------

def _ag_user(msg, ts="2026-06-02T10:00:00Z"):
    return json.dumps({"created_at": ts, "type": "USER_INPUT", "source": "USER_EXPLICIT", "content": msg}) + "\n"


def _ag_planner(text, tool=None, ts="2026-06-02T10:00:01Z"):
    ev = {"created_at": ts, "type": "PLANNER_RESPONSE", "content": text}
    if tool:
        ev["tool_calls"] = [{"name": tool, "args": {"command": "git commit -m x"}}]
    return json.dumps(ev) + "\n"


AG_SCRIPT = [
    _ag_user("first ag question"),
    _ag_planner("looking", tool="write_to_file"),
    _ag_planner("done"),
    _ag_user("second ag question"),
    _ag_planner("on it", tool="run_command"),
]


def test_antigravity_background_turn_takes_precedence_over_older_acp_idle(tmp_path, monkeypatch):
    path = tmp_path / "transcript_full.jsonl"
    idle_at = 1780394410  # 10:00:10 UTC
    now = idle_at + 60
    monkeypatch.setattr(engines.time, "time", lambda: now)
    monkeypatch.setattr(antigravity_stream, "acp_activity", lambda sid: {"busy": False, "ts": idle_at})

    def activity():
        tail = server._extract_antigravity_tail_meta(path)
        return server._antigravity_activity_fields_from_tail(tail, True, session_id="session")

    def append(event_type, second, *, source="MODEL", **fields):
        with path.open("a") as transcript:
            transcript.write(json.dumps({
                "type": event_type, "created_at": f"2026-06-02T10:00:{second:02}Z",
                "source": source, "status": "DONE", **fields,
            }) + "\n")

    append("PLANNER_RESPONSE", 5, tool_calls=[{"name": "run_command"}])
    assert activity()["sidecar_status"] == "idle"
    append("PLANNER_RESPONSE", 8, content="The requested review is done.")
    append("SYSTEM_MESSAGE", 15, source="SYSTEM", content="Background task finished.")
    assert activity()["sidecar_status"] == "idle"
    append("PLANNER_RESPONSE", 16, tool_calls=[{"name": "read_file"}])
    working = activity()
    assert working["acp_status"] == "idle"
    assert working["sidecar_in_flight"] and working["pending_tool"] == "read_file"
    assert working["working_since"] == idle_at + 6
    append("GENERIC", 22, content="Tool completed.")
    between_tools = activity()
    assert between_tools["sidecar_in_flight"] and not between_tools["pending_tool"]
    assert between_tools["sidecar_ts"] == idle_at + 12
    now += engines._ANTIGRAVITY_LIVE_WINDOW_S
    assert activity()["sidecar_status"] == "idle"
    append("PLANNER_RESPONSE", 25, content="The background work is complete.")
    finished = activity()
    assert finished["sidecar_status"] == "idle" and not finished["sidecar_in_flight"]
    assert finished["sidecar_ts"] == idle_at + 15
    assert server._extract_antigravity_tail_meta(path)["turn_complete"]


CASES = {
    "cursor": (server._extract_cursor_tail_meta if hasattr(server, "_extract_cursor_tail_meta") else None,
               CURSOR_SCRIPT, lambda: server._cursor_tail_resume),
    "antigravity": (server._extract_antigravity_tail_meta, AG_SCRIPT, lambda: server._antigravity_tail_resume),
}


def _resume_for(engine):
    return CASES[engine][2]()


@pytest.fixture(autouse=True)
def _clear_caches():
    with server._conv_meta_cache_lock:
        server._conv_meta_cache.clear()
        server._cursor_tail_resume.clear()
        server._antigravity_tail_resume.clear()
    yield
    with server._conv_meta_cache_lock:
        server._conv_meta_cache.clear()
        server._cursor_tail_resume.clear()
        server._antigravity_tail_resume.clear()


def _sans_volatile(meta):
    out = dict(meta)
    for k in ("mtime", "pending_tool_ts"):
        out.pop(k, None)
    return out


@pytest.mark.parametrize("engine", ["cursor", "antigravity"])
def test_incremental_equals_full_parse(engine, tmp_path):
    extract, script, _ = CASES[engine]

    full_path = tmp_path / "full.jsonl"
    full_path.write_text("".join(script))
    full_meta = extract(Path(full_path))

    with server._conv_meta_cache_lock:
        server._conv_meta_cache.clear()
        _resume_for(engine).clear()

    incr_path = tmp_path / "incr.jsonl"
    incr_meta = None
    with open(incr_path, "w") as fh:
        for line in script:
            fh.write(line)
            fh.flush()
            incr_meta = extract(Path(incr_path))

    assert _sans_volatile(incr_meta) == _sans_volatile(full_meta)
    # Non-trivial: the scripted turns actually registered.
    assert full_meta["first_message"]
    assert full_meta["last_prompt"]
    assert full_meta["last_event_type"]


@pytest.mark.parametrize("engine", ["cursor", "antigravity"])
def test_second_poll_reads_only_appended_bytes(engine, tmp_path):
    extract, script, _ = CASES[engine]
    p = tmp_path / "r.jsonl"
    head = "".join(script[:2])
    p.write_text(head)
    extract(Path(p))
    off1 = _resume_for(engine)[str(p)]["offset"]
    assert off1 == len(head.encode())

    tail = "".join(script[2:])
    with open(p, "a") as fh:
        fh.write(tail)
    extract(Path(p))
    off2 = _resume_for(engine)[str(p)]["offset"]
    assert off2 == len(head.encode()) + len(tail.encode())
    assert off2 > off1


@pytest.mark.parametrize("engine", ["cursor", "antigravity"])
def test_partial_trailing_line_not_consumed(engine, tmp_path):
    extract, script, _ = CASES[engine]
    p = tmp_path / "partial.jsonl"
    p.write_text(script[0] + script[1])
    extract(Path(p))
    off_complete = _resume_for(engine)[str(p)]["offset"]

    with open(p, "a") as fh:
        fh.write('{"partial": "no newline yet"')  # not terminated
    extract(Path(p))
    assert _resume_for(engine)[str(p)]["offset"] == off_complete  # not advanced

    with open(p, "a") as fh:
        fh.write("}\n")  # complete it (a no-op event, but a full line)
    extract(Path(p))
    assert _resume_for(engine)[str(p)]["offset"] > off_complete
