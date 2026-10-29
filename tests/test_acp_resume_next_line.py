"""CCC-142: ACP state rehydration counts transcript lines under _ACP_LOCK.

Iterating text lines over ~135 MB of Devin transcripts held the global ACP
lock for 15s+ at boot under GIL load, stalling the sidebar list, live builds
and conversation opens. The count must stay exact, run in binary chunks, and
read each transcript once per load.
"""
import json

import server
from ccc_server import acp


def _point_at(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_ACP_TRANSCRIPT_DIR", tmp_path / "acp")
    monkeypatch.setattr(server, "COMMAND_CENTER_STATE_DIR", tmp_path)
    (tmp_path / "acp" / "h").mkdir(parents=True)
    return tmp_path / "acp" / "h"


def test_next_line_counts_terminated_unterminated_and_missing(tmp_path, monkeypatch):
    d = _point_at(tmp_path, monkeypatch)
    (d / "a.jsonl").write_bytes(b'{"line":1}\n{"line":2}\n')
    (d / "b.jsonl").write_bytes(b'{"line":1}\n{"line":2}')
    (d / "c.jsonl").write_bytes(b"")
    assert acp._acp_resume_next_line("h", "a") == 3
    assert acp._acp_resume_next_line("h", "b") == 3
    assert acp._acp_resume_next_line("h", "c") == 1
    assert acp._acp_resume_next_line("h", "missing") == 1


def test_next_line_spans_chunk_boundaries(tmp_path, monkeypatch):
    d = _point_at(tmp_path, monkeypatch)
    line = b"x" * 1000 + b"\n"
    (d / "big.jsonl").write_bytes(line * 3000)  # ~3 MB, several 1 MB chunks
    assert acp._acp_resume_next_line("h", "big") == 3001


def test_load_state_reads_each_transcript_once(tmp_path, monkeypatch):
    d = _point_at(tmp_path, monkeypatch)
    (d / "s1.jsonl").write_bytes(b"{}\n" * 5)
    (tmp_path / "acp-h-state.json").write_text(json.dumps(
        {"sessions": {"s1": {"cwd": "/tmp", "next_line": 2}}}))
    monkeypatch.setattr(server, "_ACP_SESSION_STATE", {})
    monkeypatch.setattr(server, "_ACP_STATE_LOADED", set())
    calls = []
    real = acp._acp_resume_next_line
    monkeypatch.setattr(acp, "_acp_resume_next_line",
                        lambda h, s: calls.append(s) or real(h, s))
    with server._ACP_LOCK:
        acp._acp_load_state("h")
    assert calls == ["s1"]
    assert server._ACP_SESSION_STATE["h"]["s1"]["next_line"] == 6
