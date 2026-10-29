"""CCC-156: Archive state must survive writers in other processes.

The dashboard and ccc-worker both import server and both run the
auto-unarchive sweep. Each used to save a whole list read earlier, so a sweep
in one process silently dropped archives the other had just made.
"""
import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import server

REPO = Path(__file__).resolve().parents[1]


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ARCHIVED_CONVERSATIONS_FILE", tmp_path / "archived.json")
    monkeypatch.setattr(server, "TRASHED_CONVERSATIONS_FILE", tmp_path / "trashed.json")
    monkeypatch.setattr(server, "_archive_grace", {})
    events = []
    monkeypatch.setattr(server, "_log_archive_event", lambda *args: events.append(args))
    return events


def test_sweep_with_stale_snapshot_keeps_archives_written_meanwhile(tmp_path, monkeypatch):
    events = _isolate(tmp_path, monkeypatch)
    server._save_archived_conversations(["old-a", "live-b"])
    stale = server._load_archived_conversations(sweep=False)
    # Another process archives two sessions after this process's read.
    (tmp_path / "archived.json").write_text(json.dumps(["old-a", "live-b", "new-c", "new-d"]))

    transcript = tmp_path / "live-b.jsonl"
    transcript.write_text("{}\n")
    monkeypatch.setattr(server, "_archive_auto_sweep_last", 0.0)
    monkeypatch.setattr(server, "_discover_live_session_ids", lambda: {"live-b"})
    monkeypatch.setattr(server, "_codex_pool_candidate_sids", lambda now: frozenset())
    monkeypatch.setattr(server, "_archive_session_is_live", lambda sid: True)
    monkeypatch.setattr(server, "_resolve_conversation_reader", lambda sid: (transcript, None))

    server._auto_unarchive_live_sessions(stale)

    on_disk = json.loads((tmp_path / "archived.json").read_text())
    assert on_disk == ["old-a", "new-c", "new-d"]
    saves = [e for e in events if e[0] == "save"]
    assert saves and "why=auto-unarchive" in saves[-1][2] and "drop=live-b" in saves[-1][2]


def test_concurrent_processes_never_lose_archives(tmp_path):
    archived = tmp_path / "archived.json"
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(REPO)!r})
        import server
        from pathlib import Path
        server.ARCHIVED_CONVERSATIONS_FILE = Path({str(archived)!r})
        server.TRASHED_CONVERSATIONS_FILE = Path({str(tmp_path / "trashed.json")!r})
        tag = sys.argv[1]
        for i in range(40):
            server._update_archived_conversations(add=[f"{{tag}}-{{i}}"], reason="test")
    """)
    procs = [
        subprocess.Popen([sys.executable, "-c", script, tag], cwd=tmp_path)
        for tag in ("p1", "p2", "p3")
    ]
    for proc in procs:
        assert proc.wait(timeout=120) == 0

    on_disk = set(json.loads(archived.read_text()))
    assert on_disk == {f"{tag}-{i}" for tag in ("p1", "p2", "p3") for i in range(40)}


def test_grace_save_merges_other_process_markers(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    sticky = tmp_path / "archive-sticky.json"
    sticky.write_text(json.dumps({"dashboard-archive": 1.0}))
    server._archive_grace["worker-archive"] = 2.0

    server._save_archive_grace()

    assert json.loads(sticky.read_text()) == {"dashboard-archive": 1.0, "worker-archive": 2.0}


def test_unarchive_removal_is_not_resurrected_by_grace_merge(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    sticky = tmp_path / "archive-sticky.json"
    server._set_conversations_archived(["sid-a"], True, source="manual")
    assert "sid-a" in json.loads(sticky.read_text())

    server._set_conversations_archived(["sid-a"], False, source="manual")

    assert "sid-a" not in json.loads(sticky.read_text())


def test_sweep_honours_sticky_marker_written_by_other_process(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    server._save_archived_conversations(["manual-x"])
    # The dashboard archived it by hand; this process never saw the click.
    (tmp_path / "archive-sticky.json").write_text(json.dumps({"manual-x": time.time()}))
    transcript = tmp_path / "manual-x.jsonl"
    transcript.write_text("{}\n")
    monkeypatch.setattr(server, "_archive_auto_sweep_last", 0.0)
    monkeypatch.setattr(server, "_discover_live_session_ids", lambda: {"manual-x"})
    monkeypatch.setattr(server, "_codex_pool_candidate_sids", lambda now: frozenset())
    monkeypatch.setattr(server, "_archive_session_is_live", lambda sid: True)
    monkeypatch.setattr(server, "_resolve_conversation_reader", lambda sid: (transcript, None))

    kept = server._auto_unarchive_live_sessions(["manual-x"])

    assert kept == ["manual-x"]
    assert json.loads((tmp_path / "archived.json").read_text()) == ["manual-x"]


def test_corrupt_archive_file_is_never_overwritten_with_a_delta(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    (tmp_path / "archived.json").write_text('["a", "b"')

    try:
        server._update_archived_conversations(add=["c"], reason="test")
    except ValueError:
        pass
    else:
        raise AssertionError("delta write over a corrupt file must refuse")
    assert (tmp_path / "archived.json").read_text() == '["a", "b"'
