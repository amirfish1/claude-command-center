"""Tests for windowed conversation parsing (fast open of long transcripts).

`_parse_conversation_windowed` reads the whole JSONL (cheap) but only
json.loads + parses a slice — `tail=N` (last N lines, for the initial open) or
`before=L` (the window before line L, for "load earlier"). It returns the usual
{events,last_line} plus `first_line` and `truncated_before`.
"""
import json
from pathlib import Path

import pytest

import server


@pytest.fixture(autouse=True)
def _isolate_queued_events(monkeypatch):
    """Keep line-window tests independent of live synthetic overlays."""
    monkeypatch.setattr(
        server,
        "_get_queued_events_for_session",
        lambda _session_id: [],
    )
    monkeypatch.setattr(
        server,
        "_merge_synthetic_conversation_events",
        lambda events, _synthetic: list(events),
    )


def _write_claude_jsonl(path, n):
    """n user-message lines (each parses to exactly one event, so line# ==
    event index — keeps the window assertions deterministic)."""
    lines = []
    for i in range(1, n + 1):
        lines.append(json.dumps({
            "type": "user",
            "timestamp": f"2026-06-02T10:00:{i % 60:02d}Z",
            "message": {"role": "user", "content": f"message {i}"},
        }))
    path.write_text("\n".join(lines) + "\n")


def _full_parse(path):
    """Parse every line the same way the windowed reader does, for comparison."""
    events = []
    with open(path) as f:
        for ln, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            parsed = server._parse_conversation_event(ev, ln)
            if parsed:
                events.append(parsed)
    return events


def test_tail_returns_last_window(tmp_path):
    p = tmp_path / "c.jsonl"
    _write_claude_jsonl(p, 500)
    full = _full_parse(p)

    tw = server._parse_conversation_windowed("sid", str(p), tail=120, before=None)
    assert tw["last_line"] == 500
    assert tw["truncated_before"] is True
    # The windowed events are exactly the tail of the full parse (by line#).
    assert [e["line"] for e in tw["events"]] == [e["line"] for e in full[-len(tw["events"]):]]
    # first_line is the line# of the first returned event.
    assert tw["first_line"] == tw["events"][0]["line"]
    # window covers the last 120 LINES (events may be fewer if some lines parse
    # to nothing — here every line is an event, so exactly 120).
    assert tw["first_line"] == 381  # 500 - 120 + 1


def test_before_returns_earlier_window(tmp_path):
    p = tmp_path / "c.jsonl"
    _write_claude_jsonl(p, 500)

    tail = server._parse_conversation_windowed("sid", str(p), tail=120, before=None)
    earlier = server._parse_conversation_windowed("sid", str(p), tail=120, before=tail["first_line"])
    # Earlier window ends strictly before the tail window begins — no overlap,
    # no gap (contiguous line numbers).
    assert earlier["events"][-1]["line"] == tail["first_line"] - 1
    assert earlier["events"][0]["line"] == tail["first_line"] - 120
    assert earlier["truncated_before"] is True


def test_short_file_not_truncated(tmp_path):
    p = tmp_path / "c.jsonl"
    _write_claude_jsonl(p, 30)
    tw = server._parse_conversation_windowed("sid", str(p), tail=150, before=None)
    assert len(tw["events"]) == 30
    assert tw["first_line"] == 1
    assert tw["truncated_before"] is False


def test_parse_conversation_full_path_unchanged_without_window(tmp_path, monkeypatch):
    # Sanity: a non-windowed call still returns the whole conversation.
    p = tmp_path / "c.jsonl"
    _write_claude_jsonl(p, 40)
    monkeypatch.setattr(server, "_resolve_conversation_reader",
                        lambda cid, repo_path=None: (str(p), server._parse_conversation_event))
    monkeypatch.setattr(server, "_is_gemini_session", lambda cid: False)
    monkeypatch.setattr(server, "_is_antigravity_session", lambda cid: False)
    full = server.parse_conversation("sid", 0, use_cache=False)
    win = server.parse_conversation("sid", 0, use_cache=False, tail=10)
    assert full["last_line"] == 40
    assert len(full["events"]) == 40
    assert len(win["events"]) == 10  # windowed honored through the public API
