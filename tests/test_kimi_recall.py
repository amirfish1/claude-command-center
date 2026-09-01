from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time
from unittest import mock

from ccc_server import kimi_recall


def _write_jsonl(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def _make_kimi_home(tmp_path: Path, records):
    kimi_home = tmp_path / "kimi"
    session_dir = kimi_home / "sessions" / "project" / "session_alpha"
    _write_jsonl(
        kimi_home / "session_index.jsonl",
        [{
            "sessionId": "session_alpha",
            "sessionDir": str(session_dir),
            "workDir": "/workspace/demo",
        }],
    )
    _write_jsonl(session_dir / "agents" / "main" / "wire.jsonl", records)
    (session_dir / "state.json").write_text(json.dumps({
        "title": "Bridge work",
        "createdAt": "2026-08-31T12:00:00Z",
        "updatedAt": "2026-08-31T12:01:00Z",
    }))
    return kimi_home


def test_sync_exports_visible_kimi_conversation_and_excludes_private_events(tmp_path):
    kimi_home = _make_kimi_home(tmp_path, [
        {"type": "config.update", "modelAlias": "k3", "systemPrompt": "do not export"},
        {"type": "context.append_message", "message": {
            "role": "user", "origin": {"kind": "user"},
            "content": [{"type": "text", "text": "Ship the bridge"}],
        }},
        {"type": "context.append_loop_event", "event": {
            "type": "content.part", "part": {"type": "text", "text": "Bridge shipped."},
        }},
        {"type": "context.append_loop_event", "event": {
            "type": "content.part", "part": {"type": "think", "text": "private thought"},
        }},
        {"type": "context.append_loop_event", "event": {
            "type": "tool.call", "name": "Read", "input": {"path": "/secret"},
        }},
    ])

    output_dir = tmp_path / "knowledge"
    result = kimi_recall.sync_kimi_knowledge(output_dir, kimi_home=kimi_home)
    brief = (output_dir / "session_alpha.md").read_text(encoding="utf-8")

    assert result.exported == ["session_alpha"]
    assert "Engine: Kimi Code" in brief
    assert "Session ID: session_alpha" in brief
    assert "Project: /workspace/demo" in brief
    assert "Model: k3" in brief
    assert "Ship the bridge" in brief
    assert "Bridge shipped." in brief
    assert "do not export" not in brief
    assert "private thought" not in brief
    assert "/secret" not in brief


def test_sync_skips_unchanged_session_and_reexports_completed_wire_append(tmp_path):
    kimi_home = _make_kimi_home(tmp_path, [
        {"type": "context.append_message", "message": {
            "role": "user", "origin": {"kind": "user"},
            "content": [{"type": "text", "text": "First request"}],
        }},
    ])
    output_dir = tmp_path / "knowledge"
    wire = kimi_home / "sessions" / "project" / "session_alpha" / "agents" / "main" / "wire.jsonl"

    first = kimi_recall.sync_kimi_knowledge(output_dir, kimi_home=kimi_home)
    second = kimi_recall.sync_kimi_knowledge(output_dir, kimi_home=kimi_home)

    assert first.exported == ["session_alpha"]
    assert second.exported == []
    assert second.skipped == ["session_alpha"]

    with wire.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "context.append_loop_event", "event": {
            "type": "content.part", "part": {"type": "text", "text": "Second reply"},
        }}) + "\n")
        handle.write('{"type":"context.append_loop_event"')
    now = time.time() + 1
    os.utime(wire, (now, now))

    third = kimi_recall.sync_kimi_knowledge(output_dir, kimi_home=kimi_home)

    assert third.exported == ["session_alpha"]
    assert third.skipped == []
    assert "Second reply" in (output_dir / "session_alpha.md").read_text(encoding="utf-8")


def test_sync_writes_one_aggregate_markdown_source_for_total_recall(tmp_path):
    kimi_home = _make_kimi_home(tmp_path, [
        {"type": "context.append_message", "message": {
            "role": "user", "origin": {"kind": "user"},
            "content": [{"type": "text", "text": "Aggregate this"}],
        }},
    ])
    output_dir = tmp_path / "knowledge"

    kimi_recall.sync_kimi_knowledge(output_dir, kimi_home=kimi_home)

    aggregate = (output_dir / "kimi-code-sessions.md").read_text(encoding="utf-8")
    assert aggregate.startswith("# Kimi Code Knowledge Export")
    assert "Session ID: session_alpha" in aggregate
    assert "Aggregate this" in aggregate


def test_launchd_plist_runs_sync_with_explicit_paths():
    plist = kimi_recall.launchd_plist(
        script_path="/opt/ccc/scripts/kimi-recall-bridge.py",
        output_dir="/tmp/kimi-knowledge",
        kimi_home="/tmp/kimi-home",
        interval_seconds=300,
    )

    assert plist["Label"] == "com.github.claude-command-center.kimi-recall-bridge"
    assert plist["StartInterval"] == 300
    assert plist["ProgramArguments"] == [
        sys.executable,
        "/opt/ccc/scripts/kimi-recall-bridge.py",
        "sync",
        "--output-dir",
        "/tmp/kimi-knowledge",
        "--kimi-home",
        "/tmp/kimi-home",
    ]


def test_connect_total_recall_runs_supported_folder_ingest(tmp_path):
    output_dir = tmp_path / "knowledge"
    output_dir.mkdir()
    (output_dir / "kimi-code-sessions.md").write_text("# Kimi Code Knowledge Export\n", encoding="utf-8")
    with mock.patch("ccc_server.kimi_recall.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess([], 0, "connected", "")
        result = kimi_recall.connect_total_recall(output_dir, executable="total-recall")

    assert result.returncode == 0
    run.assert_called_once_with(
        ["total-recall", "ingest", str(output_dir / "kimi-code-sessions.md")],
        capture_output=True,
        text=True,
        check=False,
    )


def test_bridge_cli_sync_exports_knowledge_document(tmp_path):
    kimi_home = _make_kimi_home(tmp_path, [
        {"type": "context.append_message", "message": {
            "role": "user", "origin": {"kind": "user"},
            "content": [{"type": "text", "text": "Use the CLI"}],
        }},
    ])
    output_dir = tmp_path / "knowledge"
    script = Path(__file__).resolve().parents[1] / "scripts" / "kimi-recall-bridge.py"

    result = subprocess.run(
        [sys.executable, str(script), "sync", "--output-dir", str(output_dir),
         "--kimi-home", str(kimi_home)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["exported"] == ["session_alpha"]
    assert (output_dir / "session_alpha.md").is_file()


def test_install_launchd_writes_the_opt_in_sync_job(tmp_path):
    destination = tmp_path / "com.github.claude-command-center.kimi-recall-bridge.plist"

    installed = kimi_recall.install_launchd(
        script_path="/opt/ccc/scripts/kimi-recall-bridge.py",
        output_dir="/tmp/kimi-knowledge",
        kimi_home="/tmp/kimi-home",
        destination=destination,
    )

    assert installed == destination
    with destination.open("rb") as handle:
        plist = plistlib.load(handle)
    assert plist["Label"] == "com.github.claude-command-center.kimi-recall-bridge"
    assert plist["ProgramArguments"][2] == "sync"


def test_load_launchd_bootstraps_the_written_job(tmp_path):
    destination = tmp_path / "bridge.plist"
    with mock.patch("ccc_server.kimi_recall.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        result = kimi_recall.load_launchd(destination, user_id=501)

    assert result.returncode == 0
    assert run.call_args_list[0].args[0] == [
        "launchctl", "bootout", "gui/501/com.github.claude-command-center.kimi-recall-bridge",
    ]
    assert run.call_args_list[1].args[0] == [
        "launchctl", "bootstrap", "gui/501", str(destination),
    ]


def test_sync_never_allows_a_session_id_to_escape_the_knowledge_folder(tmp_path):
    kimi_home = _make_kimi_home(tmp_path, [])
    index = kimi_home / "session_index.jsonl"
    index.write_text(json.dumps({
        "sessionId": "../../outside",
        "sessionDir": str(kimi_home / "sessions" / "project" / "session_alpha"),
        "workDir": "/workspace/demo",
    }) + "\n", encoding="utf-8")
    output_dir = tmp_path / "knowledge"

    kimi_recall.sync_kimi_knowledge(output_dir, kimi_home=kimi_home)

    assert not (tmp_path.parent / "outside.md").exists()
    assert all(path.parent.resolve() == output_dir.resolve() for path in output_dir.glob("*.md"))


def test_sync_reexports_when_session_metadata_changes_without_wire_change(tmp_path):
    kimi_home = _make_kimi_home(tmp_path, [])
    output_dir = tmp_path / "knowledge"
    state_path = kimi_home / "sessions" / "project" / "session_alpha" / "state.json"

    kimi_recall.sync_kimi_knowledge(output_dir, kimi_home=kimi_home)
    state_path.write_text(json.dumps({"title": "New title", "updatedAt": "2026-08-31T12:02:00Z"}), encoding="utf-8")

    result = kimi_recall.sync_kimi_knowledge(output_dir, kimi_home=kimi_home)

    assert result.exported == ["session_alpha"]
    assert "# Kimi Code session: New title" in (output_dir / "session_alpha.md").read_text(encoding="utf-8")


def test_connect_total_recall_returns_controlled_error_when_cli_is_missing(tmp_path):
    (tmp_path / "kimi-code-sessions.md").write_text("# Kimi Code Knowledge Export\n", encoding="utf-8")
    with mock.patch("ccc_server.kimi_recall.subprocess.run", side_effect=FileNotFoundError):
        result = kimi_recall.connect_total_recall(tmp_path, executable="missing-total-recall")

    assert result.returncode == 127
    assert "missing-total-recall" in result.stderr


def test_connect_kimi_knowledge_does_not_connect_after_export_errors(tmp_path):
    failed = kimi_recall.SyncResult(exported=[], skipped=[], errors=["session_alpha: permission denied"])
    with mock.patch("ccc_server.kimi_recall.sync_kimi_knowledge", return_value=failed), \
         mock.patch("ccc_server.kimi_recall.connect_total_recall") as connect:
        result, connection = kimi_recall.connect_kimi_knowledge(tmp_path)

    assert result is failed
    assert connection is None
    connect.assert_not_called()
