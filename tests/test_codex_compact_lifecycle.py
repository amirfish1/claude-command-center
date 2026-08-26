"""Codex compaction lifecycle: post-compact sizing, boundary row, and the wait.

Three bugs shipped together and are covered together here:

1. The two Codex usage extractors disagreed about the token_count Codex writes
   right after a compaction (per-turn fields all zero, the rebuilt context size
   in ``total_tokens`` only). One read the zero, the other skipped the record
   and kept the stale pre-compact number.
2. Codex compactions produced no ``compact_boundary`` transcript row at all.
3. ``_codex_compact_via_app_server`` returned as soon as the app-server ACKed
   ``thread/compact/start``, so the UI card claimed the compaction was over
   while the compaction turn was still running.

Fixture shapes are synthesized from the real rollout record layout: no session
content is copied in.
"""

import json
import threading
import time

import pytest

import server


COMPACTED_RECORD = {
    "timestamp": "2026-08-24T20:41:25.432Z",
    "type": "compacted",
    "payload": {"message": "", "replacement_history": []},
}
CONTEXT_COMPACTED_RECORD = {
    "timestamp": "2026-08-24T20:41:25.712Z",
    "type": "event_msg",
    "payload": {"type": "context_compacted"},
}


def _token_count(input_tokens, total_tokens, *, output_tokens=0, lifetime=None):
    """One Codex ``token_count`` event_msg."""
    lifetime = lifetime or {
        "input_tokens": input_tokens,
        "cached_input_tokens": 0,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    return {
        "timestamp": "2026-08-24T20:41:25.577Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": 0,
                    "cache_write_input_tokens": 0,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": 0,
                    "total_tokens": total_tokens,
                },
                "total_token_usage": lifetime,
                "model_context_window": 828_400,
            },
        },
    }


def _write_rollout(path, events):
    path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )
    return path


# The shape the real incident produced: a big turn, then the compaction turn
# whose token_count reports 0 input / 12211 total.
POST_COMPACT_ROLLOUT = [
    {"type": "turn_context", "payload": {"model": "gpt-5.6-sol"}},
    _token_count(282_000, 282_900, output_tokens=900),
    {
        "timestamp": "2026-08-24T20:40:45.921Z",
        "type": "event_msg",
        "payload": {"type": "task_started", "turn_id": "turn-1"},
    },
    COMPACTED_RECORD,
    _token_count(0, 12_211, lifetime={
        "input_tokens": 300_000,
        "cached_input_tokens": 0,
        "output_tokens": 900,
        "total_tokens": 320_000,
    }),
    CONTEXT_COMPACTED_RECORD,
    {
        "timestamp": "2026-08-24T20:41:27.558Z",
        "type": "event_msg",
        "payload": {
            "type": "task_complete",
            "turn_id": "turn-1",
            "last_agent_message": None,
            "duration_ms": 41_658,
        },
    },
]


def test_extract_codex_usage_reads_post_compact_size_from_total_tokens(
    tmp_path, monkeypatch
):
    rollout = _write_rollout(tmp_path / "rollout.jsonl", POST_COMPACT_ROLLOUT)
    monkeypatch.setattr(server, "_resolve_codex_rollout_path", lambda _: rollout)
    monkeypatch.setattr(server, "_codex_thread_row", lambda _: {})

    usage = server._extract_codex_usage("codex-compact-session")

    # The zeroed post-compact reading is the NEW context size, not "ctx 0".
    assert usage["latest_input_tokens"] == 12_211
    # ...and it must not drag the peak down.
    assert usage["peak_input_tokens"] == 282_000


def test_extract_codex_usage_ignores_a_fully_zero_token_count(tmp_path, monkeypatch):
    rollout = _write_rollout(tmp_path / "rollout.jsonl", [
        {"type": "turn_context", "payload": {"model": "gpt-5.6-sol"}},
        _token_count(282_000, 282_900, output_tokens=900),
        _token_count(0, 0),
    ])
    monkeypatch.setattr(server, "_resolve_codex_rollout_path", lambda _: rollout)
    monkeypatch.setattr(server, "_codex_thread_row", lambda _: {})

    usage = server._extract_codex_usage("codex-zero-session")

    assert usage["latest_input_tokens"] == 282_000
    assert usage["peak_input_tokens"] == 282_000


def test_extract_codex_tail_meta_agrees_with_the_usage_extractor(tmp_path):
    rollout = _write_rollout(tmp_path / "rollout.jsonl", POST_COMPACT_ROLLOUT)

    meta = server._extract_codex_tail_meta(rollout)

    assert meta["latest_input_tokens"] == 12_211
    assert meta["context_limit"] == 828_400


def test_extract_codex_tail_meta_ignores_a_fully_zero_token_count(tmp_path):
    rollout = _write_rollout(tmp_path / "rollout.jsonl", [
        _token_count(282_000, 282_900, output_tokens=900),
        _token_count(0, 0),
    ])

    meta = server._extract_codex_tail_meta(rollout)

    assert meta["latest_input_tokens"] == 282_000


def _boundary_events(rollout, monkeypatch, windowed=False):
    monkeypatch.setattr(
        server, "_enrich_codex_no_agent_output_events", lambda _cid, events: events
    )
    if windowed:
        parsed = server._parse_conversation_windowed(
            "codex-boundary", rollout, 100, None, server._parse_codex_event
        )
    else:
        monkeypatch.setattr(
            server,
            "_resolve_conversation_reader",
            lambda cid, repo_path=None: (rollout, server._parse_codex_event),
        )
        parsed = server.parse_conversation("codex-boundary", use_cache=False)
    return [
        event for event in parsed["events"]
        if event.get("subtype") == "compact_boundary"
    ]


@pytest.mark.parametrize("windowed", [False, True])
def test_codex_parser_emits_one_compact_boundary_with_real_numbers(
    tmp_path, monkeypatch, windowed
):
    rollout = _write_rollout(tmp_path / "rollout.jsonl", POST_COMPACT_ROLLOUT)

    boundaries = _boundary_events(rollout, monkeypatch, windowed=windowed)

    # The `compacted` record and the `context_compacted` marker describe ONE
    # compaction, so they must fold into a single row.
    assert len(boundaries) == 1
    boundary = boundaries[0]
    assert boundary["type"] == "system"
    assert boundary["engine"] == "codex"
    assert boundary["compact"] == {
        "trigger": "manual",
        "pre_tokens": 282_000,
        "post_tokens": 12_211,
        "duration_ms": 41_658,
    }


def test_codex_parser_emits_a_boundary_without_the_context_compacted_marker(
    tmp_path, monkeypatch
):
    # A handful of rollouts carry the top-level record with no marker after it.
    rollout = _write_rollout(tmp_path / "rollout.jsonl", [
        event for event in POST_COMPACT_ROLLOUT
        if event is not CONTEXT_COMPACTED_RECORD
    ])

    boundaries = _boundary_events(rollout, monkeypatch)

    assert len(boundaries) == 1
    assert boundaries[0]["compact"]["pre_tokens"] == 282_000
    # Filled in by the token_count that follows the `compacted` record.
    assert boundaries[0]["compact"]["post_tokens"] == 12_211


def _stub_app_server(monkeypatch, rollout):
    monkeypatch.setattr(server, "_resolve_codex_rollout_path", lambda _: rollout)
    monkeypatch.setattr(
        server, "_codex_app_server_request",
        lambda method, params, timeout=None: {"ok": True, "result": {}},
    )
    monkeypatch.setattr(server, "_codex_response_succeeded", lambda _resp: True)
    monkeypatch.setattr(server, "_codex_app_server_thread_state", lambda _sid: {})


def test_codex_compact_waits_for_the_compaction_to_land(tmp_path, monkeypatch):
    rollout = _write_rollout(tmp_path / "rollout.jsonl", [
        _token_count(282_000, 282_900, output_tokens=900),
    ])
    _stub_app_server(monkeypatch, rollout)
    monkeypatch.setenv("CCC_CODEX_COMPACT_WAIT_S", "20")

    def _finish_compaction():
        time.sleep(0.8)
        with rollout.open("a", encoding="utf-8") as handle:
            for event in (
                COMPACTED_RECORD,
                _token_count(0, 12_211),
                CONTEXT_COMPACTED_RECORD,
            ):
                handle.write(json.dumps(event) + "\n")

    writer = threading.Thread(target=_finish_compaction, daemon=True)
    writer.start()
    started = time.time()
    result = server._codex_compact_via_app_server("codex-wait-session")
    elapsed = time.time() - started
    writer.join(timeout=5)

    assert result["ok"] is True
    assert result["status"] == "compacted"
    assert result["compact_result"] == "success"
    assert result["via"] == "codex-compact"
    assert result["pre_tokens"] == 282_000
    assert result["post_tokens"] == 12_211
    # The clock must span the real wait, not the sub-second RPC ack.
    assert elapsed >= 0.8
    assert result["duration_ms"] >= 800


def test_codex_compact_reports_compact_timeout_on_the_deadline(tmp_path, monkeypatch):
    rollout = _write_rollout(tmp_path / "rollout.jsonl", [
        _token_count(282_000, 282_900, output_tokens=900),
    ])
    _stub_app_server(monkeypatch, rollout)
    monkeypatch.setenv("CCC_CODEX_COMPACT_WAIT_S", "1")

    result = server._codex_compact_via_app_server("codex-timeout-session")

    # Not a failure: the frontend keeps the card working on this code.
    assert result["ok"] is False
    assert result["code"] == "compact_timeout"
    assert result["status"] == "compacting"
    assert result["pre_tokens"] == 282_000
    assert "still compacting" in result["error"]
