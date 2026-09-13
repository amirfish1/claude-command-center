"""Unit tests for `_history_index/parse.py`.

The history indexer's parser turns Claude Code and Codex JSONL transcripts
into the flat record dicts the rest of the ingest pipeline (embed → SQLite →
search) consumes. It was one of the least-covered modules in the repo even
though every indexed message goes through it, so a shape regression would
silently drop content from `/api/history/search` results rather than fail
loudly.

Pure functions, no server import, no network: every case is a literal record
dict or a JSONL file written into pytest's tmp_path.
"""
import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from _history_index import parse  # noqa: E402


def _write_jsonl(path, records):
    path.write_text(
        "\n".join(json.dumps(r) if isinstance(r, dict) else r for r in records) + "\n",
        encoding="utf-8",
    )
    return str(path)


# --------------------------------------------------------------------------
# extract_text — Claude Code records
# --------------------------------------------------------------------------

def test_unsearchable_record_types_are_skipped():
    assert parse.extract_text({"type": "system", "message": {"content": "hi"}}) is None
    assert parse.extract_text({}) is None


@pytest.mark.parametrize(
    "record,expected",
    [
        ({"type": "summary", "summary": "the summary"}, "the summary"),
        ({"type": "summary", "content": "fallback"}, "fallback"),
        ({"type": "custom-title", "title": "my title"}, "my title"),
        ({"type": "custom-title", "content": "fallback"}, "fallback"),
        ({"type": "last-prompt", "prompt": "do the thing"}, "do the thing"),
        ({"type": "last-prompt", "content": "fallback"}, "fallback"),
    ],
)
def test_sidecar_record_types_read_their_own_field_then_content(record, expected):
    assert parse.extract_text(record) == expected


def test_string_content_is_returned_verbatim():
    assert parse.extract_text({"type": "user", "message": {"content": "hello"}}) == "hello"


def test_missing_or_non_list_content_yields_none():
    assert parse.extract_text({"type": "user", "message": {}}) is None
    assert parse.extract_text({"type": "user"}) is None
    assert parse.extract_text({"type": "assistant", "message": {"content": 42}}) is None


def test_block_list_joins_text_thinking_and_images():
    text = parse.extract_text({
        "type": "assistant",
        "message": {"content": [
            {"type": "text", "text": "first"},
            {"type": "text", "text": ""},          # empty blocks contribute nothing
            "not-a-dict",                            # non-dict blocks are skipped
            {"type": "thinking", "thinking": "pondering"},
            {"type": "image", "source": {}},
            {"type": "unknown", "text": "ignored"},
        ]},
    })
    assert text == "first\npondering\n[image]"


def test_tool_use_block_renders_name_and_json_input():
    text = parse.extract_text({
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
        ]},
    })
    assert text == '[tool_use:Bash] {"command": "ls -la"}'


def test_tool_use_block_with_unserialisable_input_falls_back_to_str():
    text = parse.extract_text({
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "X", "input": {1, 2}}]},
    })
    assert text.startswith("[tool_use:X] ")
    assert "1" in text and "2" in text


def test_tool_result_block_handles_both_list_and_string_content():
    listed = parse.extract_text({
        "type": "user",
        "message": {"content": [
            {"type": "tool_result", "content": [
                {"type": "text", "text": "line one"},
                {"type": "image"},
                "raw",
            ]},
        ]},
    })
    assert listed == "line one"

    plain = parse.extract_text({
        "type": "user",
        "message": {"content": [{"type": "tool_result", "content": "just text"}]},
    })
    assert plain == "just text"


def test_blocks_are_truncated_at_the_block_ceiling():
    long_thought = "x" * (parse.MAX_BLOCK_CHARS + 500)
    text = parse.extract_text({
        "type": "assistant",
        "message": {"content": [{"type": "thinking", "thinking": long_thought}]},
    })
    assert len(text) == parse.MAX_BLOCK_CHARS + 1
    assert text.endswith("…")


def test_text_blocks_are_not_truncated():
    long_text = "y" * (parse.MAX_BLOCK_CHARS + 500)
    text = parse.extract_text({
        "type": "assistant", "message": {"content": [{"type": "text", "text": long_text}]},
    })
    assert text == long_text


def test_content_with_no_usable_blocks_yields_none():
    assert parse.extract_text({
        "type": "assistant", "message": {"content": [{"type": "image_ref"}]},
    }) is None


# --------------------------------------------------------------------------
# parse_line / iter_records
# --------------------------------------------------------------------------

def test_parse_line_maps_the_full_record_shape():
    rec = parse.parse_line(
        json.dumps({
            "uuid": "u-1",
            "sessionId": "s-1",
            "parentUuid": "u-0",
            "type": "assistant",
            "cwd": "/repo",
            "gitBranch": "main",
            "timestamp": "2026-01-01T00:00:00Z",
            "version": "1.2.3",
            "slug": "slug-1",
            "message": {"role": "assistant", "model": "sonnet", "content": "hi"},
        }),
        source_file="/tmp/a.jsonl",
        source_line=7,
        project_dir="-repo",
    )
    assert rec == {
        "uuid": "u-1",
        "session_id": "s-1",
        "parent_uuid": "u-0",
        "type": "assistant",
        "role": "assistant",
        "cwd": "/repo",
        "project_dir": "-repo",
        "git_branch": "main",
        "timestamp": "2026-01-01T00:00:00Z",
        "version": "1.2.3",
        "slug": "slug-1",
        "model": "sonnet",
        "source_file": "/tmp/a.jsonl",
        "source_line": 7,
        "content": "hi",
    }


def test_parse_line_falls_back_to_record_type_for_role():
    rec = parse.parse_line(
        json.dumps({"type": "summary", "summary": "s"}), "f", 0, "p")
    assert rec["role"] == "summary"
    assert rec["model"] is None


@pytest.mark.parametrize("line", ["", "   \n", "{not json", json.dumps({"type": "system"})])
def test_parse_line_returns_none_for_unindexable_lines(line):
    assert parse.parse_line(line, "f", 0, "p") is None


def test_iter_records_skips_blank_and_unparseable_lines_and_honours_start_line(tmp_path):
    path = _write_jsonl(tmp_path / "session.jsonl", [
        {"type": "user", "uuid": "a", "message": {"content": "zero"}},
        "",
        "{broken",
        {"type": "system", "message": {"content": "not searchable"}},
        {"type": "assistant", "uuid": "b", "message": {"content": "four"}},
    ])

    all_recs = list(parse.iter_records(path, "-proj"))
    assert [(r["uuid"], r["content"], r["source_line"]) for r in all_recs] == [
        ("a", "zero", 0), ("b", "four", 4),
    ]
    assert all(r["project_dir"] == "-proj" for r in all_recs)

    resumed = list(parse.iter_records(path, "-proj", start_line=1))
    assert [r["uuid"] for r in resumed] == ["b"]


# --------------------------------------------------------------------------
# extract_codex_text
# --------------------------------------------------------------------------

def _codex_message(role="user", blocks=None, rtype="response_item", ptype="message"):
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "type": rtype,
        "payload": {"type": ptype, "role": role, "content": blocks if blocks is not None else []},
    }


def test_codex_text_reads_every_text_block_kind():
    rec = _codex_message(blocks=[
        {"type": "input_text", "text": "asked"},
        {"type": "output_text", "text": "answered"},
        {"type": "text", "text": "plain"},
        {"type": "input_image", "text": "ignored"},
        {"type": "output_text", "text": ""},
        "not-a-dict",
    ])
    assert parse.extract_codex_text(rec) == "asked\nanswered\nplain"


@pytest.mark.parametrize("rec", [
    _codex_message(rtype="event_msg"),
    _codex_message(ptype="function_call"),
    _codex_message(role="tool", blocks=[{"type": "text", "text": "t"}]),
    _codex_message(blocks="a string"),
    _codex_message(blocks=[{"type": "input_image"}]),
    {"type": "response_item", "payload": "not-a-dict"},
])
def test_codex_text_skips_non_message_records(rec):
    assert parse.extract_codex_text(rec) is None


def test_codex_text_truncates_long_blocks():
    rec = _codex_message(blocks=[{"type": "output_text", "text": "z" * 5000}])
    out = parse.extract_codex_text(rec)
    assert len(out) == parse.MAX_BLOCK_CHARS + 1 and out.endswith("…")


# --------------------------------------------------------------------------
# iter_records_codex
# --------------------------------------------------------------------------

def test_codex_records_carry_synthetic_uuids_and_tracked_cwd(tmp_path):
    path = _write_jsonl(tmp_path / "rollout.jsonl", [
        {"type": "session_meta", "payload": {"cwd": "/repo/one", "id": "sess-from-meta"}},
        _codex_message(role="user", blocks=[{"type": "input_text", "text": "hello"}]),
        {"type": "turn_context", "payload": {"cwd": "/repo/two"}},
        _codex_message(role="assistant", blocks=[{"type": "output_text", "text": "hi"}]),
        {"type": "event_msg", "payload": {"type": "agent_message"}},
    ])

    recs = list(parse.iter_records_codex(path, session_id=""))
    assert [r["uuid"] for r in recs] == [
        "codex:sess-from-meta:000001", "codex:sess-from-meta:000003",
    ]
    assert [r["cwd"] for r in recs] == ["/repo/one", "/repo/two"]
    assert [r["role"] for r in recs] == ["user", "assistant"]
    assert [r["type"] for r in recs] == ["user", "assistant"]
    assert all(r["project_dir"] == "_codex" for r in recs)
    assert recs[0]["source_file"] == path and recs[0]["source_line"] == 1
    assert recs[0]["timestamp"] == "2026-01-01T00:00:00Z"


def test_codex_explicit_session_id_is_not_overwritten_by_meta(tmp_path):
    path = _write_jsonl(tmp_path / "rollout.jsonl", [
        {"type": "session_meta", "payload": {"cwd": "/repo", "id": "meta-id"}},
        _codex_message(blocks=[{"type": "input_text", "text": "hello"}]),
    ])
    recs = list(parse.iter_records_codex(path, session_id="caller-id"))
    assert [r["session_id"] for r in recs] == ["caller-id"]


def test_codex_resumed_ingest_still_sees_cwd_set_before_start_line(tmp_path):
    path = _write_jsonl(tmp_path / "rollout.jsonl", [
        {"type": "session_meta", "payload": {"cwd": "/repo/root", "id": "s"}},
        _codex_message(blocks=[{"type": "input_text", "text": "first"}]),
        _codex_message(role="assistant", blocks=[{"type": "output_text", "text": "second"}]),
    ])

    recs = list(parse.iter_records_codex(path, session_id="s", start_line=2))
    assert [r["content"] for r in recs] == ["second"]
    assert recs[0]["cwd"] == "/repo/root", "cwd from line 0 must survive a resumed ingest"


def test_codex_blank_broken_and_empty_message_lines_are_dropped(tmp_path):
    path = _write_jsonl(tmp_path / "rollout.jsonl", [
        "",
        "{broken",
        _codex_message(blocks=[]),
        _codex_message(blocks=[{"type": "output_text", "text": "kept"}]),
    ])
    recs = list(parse.iter_records_codex(path, session_id="s"))
    assert [r["content"] for r in recs] == ["kept"]


def test_codex_uuids_are_stable_across_reingest(tmp_path):
    path = _write_jsonl(tmp_path / "rollout.jsonl", [
        _codex_message(blocks=[{"type": "input_text", "text": "one"}]),
        _codex_message(role="assistant", blocks=[{"type": "output_text", "text": "two"}]),
    ])
    first = [r["uuid"] for r in parse.iter_records_codex(path, session_id="s")]
    second = [r["uuid"] for r in parse.iter_records_codex(path, session_id="s")]
    assert first == second == ["codex:s:000000", "codex:s:000001"]
