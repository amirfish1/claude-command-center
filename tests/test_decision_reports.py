"""The report bundles observed UI and independently read transcript evidence."""

import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
import server
from ccc_server import decision_reports as reports


@pytest.fixture
def report_env(tmp_path, monkeypatch):
    answer = '<session-state>\nNEXT_STEP_USER: Choose: keep all or shorten.\n</session-state>'
    events = [{"type": "assistant", "ts": "2026-01-01T10:00:00Z", "line": 24,
               "blocks": [{"type": "text", "text": answer}]}]
    queue = mock.Mock()
    queue.enqueue.return_value = {"ref": "DECISIONS-FIX-1", "project": "DECISIONS-FIX"}
    monkeypatch.setattr(server, "COMMAND_CENTER_STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "conversation_transcript_path", lambda sid: "/workspace/transcripts/" + sid + ".jsonl")
    parser = mock.Mock(return_value={"events": events, "last_line": 24})
    monkeypatch.setattr(server, "parse_conversation", parser)
    monkeypatch.setattr(server, "_detect_session_engine", lambda sid: "codex")
    monkeypatch.setattr(server, "find_session_cwd", lambda sid: "/workspace/project")
    monkeypatch.setattr(server, "_SERVER_CODE_REV", "test-revision")
    annotation = mock.Mock(return_value={"ok": True, "annotation": {
        "id": "ann-test", "screenshot_path": str(tmp_path / "row.png")}})
    monkeypatch.setattr(server, "create_annotation", annotation)
    monkeypatch.setattr(server, "_q", queue)
    monkeypatch.setattr(server, "_WT_WORKERS_AVAILABLE", False)
    payload = {"session_id": "test-session", "url": "http://localhost/",
               "visible_row": {"title": "Workspace: repair", "text": "Waiting", "actions": []},
               "client_state": {"session_state": {"next_step_user": "None."}},
               "latest_assistant_text": "An answer seen through the live stream",
               "screenshot_b64": "fake image handled by annotation mock"}
    return SimpleNamespace(payload=payload, queue=queue, parser=parser, annotation=annotation, answer=answer)


def test_report_keeps_observed_and_fresh_evidence_separate(report_env):
    env = report_env
    response, status = reports.create_report(env.payload)
    assert status == 200 and response["ref"] == "DECISIONS-FIX-1"
    env.parser.assert_called_once_with("test-session", tail=200, use_cache=False)
    evidence = json.loads(Path(response["evidence_path"]).read_text())
    assert evidence["visible_row"]["text"] == "Waiting"
    assert evidence["latest_transcript_assistant"]["text"] == env.answer
    assert evidence["fresh_session_state"]["next_step_user"] == "Choose: keep all or shorten."
    assert evidence["client_latest_assistant_text"] == env.payload["latest_assistant_text"]
    assert evidence["engine"] == "codex"
    assert evidence["transcript_path"].endswith("test-session.jsonl")
    assert Path(response["evidence_path"]).stat().st_mode & 0o777 == 0o600
    args = env.queue.enqueue.call_args.kwargs
    assert args["project"] == "DECISIONS-FIX" and args["item_type"] == "bug"
    assert args["repo_path"] == str(server.CCC_ROOT)
    assert response["evidence_path"] in args["text"]
    assert "Do not send replies" in args["text"]


def test_repeated_unchanged_reports_share_a_queue_dedupe_key(report_env):
    reports.create_report(report_env.payload)
    first = report_env.queue.enqueue.call_args.kwargs["dedupe_key"]
    reports.create_report(report_env.payload)
    assert report_env.queue.enqueue.call_args.kwargs["dedupe_key"] == first
    report_env.payload["visible_row"]["text"] = "Different wrong status"
    reports.create_report(report_env.payload)
    assert report_env.queue.enqueue.call_args.kwargs["dedupe_key"] != first


def test_queue_failure_is_not_reported_as_success_and_keeps_evidence(report_env):
    report_env.queue.enqueue.side_effect = RuntimeError("queue unavailable")
    result, status = reports.create_report(report_env.payload)
    assert status == 503 and not result["ok"]
    assert Path(result["evidence_path"]).is_file()
    assert "ticket filing failed" in result["error"]


def test_capture_and_transcript_failure_still_file_useful_browser_evidence(report_env, monkeypatch):
    report_env.parser.side_effect = OSError("transcript unavailable")
    report_env.annotation.return_value = {"ok": True, "annotation": {"screenshot_warning": "capture failed"}}
    monkeypatch.setattr(server, "conversation_transcript_path", lambda sid: "")
    result, status = reports.create_report(report_env.payload)
    assert status == 200 and result["screenshot_warning"] == "capture failed"
    evidence = json.loads(Path(result["evidence_path"]).read_text())
    assert "unavailable" in evidence["transcript_warning"]
    assert evidence["client_latest_assistant_text"]
    assert evidence["latest_transcript_assistant"] is None


def test_browser_cannot_choose_transcript_or_ticket_repository(report_env):
    report_env.payload.update(transcript_path="/etc/passwd", repo_path="/untrusted", native_app=False)
    reports.create_report(report_env.payload)
    assert report_env.annotation.call_args.args[0]["capture_screen"] is False
    assert report_env.queue.enqueue.call_args.kwargs["repo_path"] == str(server.CCC_ROOT)


@pytest.mark.parametrize("payload", [None, [], {}, {"session_id": "../../escape"},
                                      {"session_id": "ok", "visible_row": {"x": "x" * 200001}}])
def test_invalid_reports_are_rejected_before_capture_or_queue(report_env, payload):
    result, status = reports.create_report(payload)
    assert status == 400 and not result["ok"]
    report_env.annotation.assert_not_called()
    report_env.queue.enqueue.assert_not_called()


def test_request_size_is_bounded_without_reading_body():
    handler = SimpleNamespace(headers={"Content-Length": str(reports.MAX_REQUEST + 1)},
                              rfile=io.BytesIO(b"unread"), send_json=mock.Mock())
    reports.handle_post(handler)
    assert handler.send_json.call_args.args[1] == 413
    assert handler.rfile.tell() == 0
