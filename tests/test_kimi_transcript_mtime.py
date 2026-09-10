"""Regression coverage for Kimi freshness in the cold-session composer."""

import time

from unittest import mock

import server


def test_kimi_acp_status_exposes_wire_transcript_mtime(tmp_path):
    """The open-pane poll must supersede a stale sidebar timestamp."""
    wire = tmp_path / "wire.jsonl"
    wire.write_text('{"type":"step.end"}\n', encoding="utf-8")
    expected_mtime = time.time() - 5
    wire.touch()
    # Use a known timestamp so the assertion proves the field is sourced from
    # the current Kimi wire transcript rather than the cached conversation row.
    import os
    os.utime(wire, (expected_mtime, expected_mtime))

    with mock.patch.object(server, "_is_kimi_session", return_value=True), \
         mock.patch("ccc_server.kap.kap_routes", return_value=False), \
         mock.patch.object(server, "_acp_resolve_bin", return_value={"available": True}), \
         mock.patch.object(server, "_acp_session_snapshot", return_value={"status": "idle"}), \
         mock.patch.object(server, "_kimi_wire_turn_active", return_value=False), \
         mock.patch.object(server, "_acp_wire_path", return_value=wire), \
         mock.patch.object(server, "_kimi_session_index", return_value={}), \
         mock.patch.object(server, "_kimi_wire_tail_meta", return_value={}), \
         mock.patch.object(server, "_kimi_stale_tool_fields", return_value={}):
        status = server.session_live_status("kimi-freshness", "/tmp/repo")

    assert status["transcript_mtime"] == expected_mtime
