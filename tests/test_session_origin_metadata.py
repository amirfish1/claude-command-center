import json

import pytest

from ccc_server.session_origin import launch_metadata


def test_marker_survives_missing_transcript_and_overrides_registry(tmp_path):
    result = launch_metadata("child", {"child": {"spawned_via": "api", "kind": "review",
        "caller": "review-tool", "parent_session_id": "parent"}}, tmp_path / "missing",
        {"spawned_via": "ui", "parent_session_id": "old-parent"})
    assert result == {"session_id": "child", "spawned_via": "api", "spawned_kind": "review",
                      "spawn_caller": "review-tool", "parent_session_id": "parent"}


def test_reads_native_parent_and_skips_instruction_preamble(tmp_path):
    path = tmp_path / "session.jsonl"
    events = [
        {"type": "session_meta", "payload": {"cwd": "/tmp/te-side-example", "originator": "codex_exec",
            "source": {"subagent": {"thread_spawn": {"parent_thread_id": "parent"}}}}},
        {"type": "response_item", "payload": {"role": "user", "content": [{"text": "# AGENTS.md instructions"}]}},
        {"type": "response_item", "payload": {"role": "user", "content": [{"text": "You monitor an AI coding agent that is partway through ONE task"}]}},
    ]
    path.write_text("malformed\n" + "\n".join(map(json.dumps, events)))
    result = launch_metadata("child", {}, path)
    assert result["parent_session_id"] == "parent"
    assert result["launch_originator"] == "codex_exec"
    assert result["session_cwd"] == "/tmp/te-side-example"
    assert result["first_message"].startswith("You monitor")


def test_read_is_bounded_and_does_not_invent_missing_fields(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text("x" * 262145 + "\n" + json.dumps({"cwd": "/outside-budget"}))
    assert launch_metadata("child", {}, path) == {"session_id": "child"}
    path.write_text(json.dumps({"type": "user", "message": {"content": "a" * 1000}}))
    assert len(launch_metadata("child", {}, path)["first_message"]) == 200


@pytest.mark.parametrize("sid", ["../escape", "", "a/b", "a" * 201])
def test_rejects_invalid_session_ids(sid):
    with pytest.raises(ValueError):
        launch_metadata(sid, {})


def test_registry_without_marker_and_malformed_native_source(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(json.dumps({"type": "session_meta", "payload": {
        "source": {"subagent": {"thread_spawn": "unexpected"}}}}))
    assert launch_metadata("child", {}, path, {"spawned_via": "cli"}) == {
        "session_id": "child", "spawned_via": "cli"}


def test_worker_ledger_supplies_purpose_and_parent_without_transcript():
    origin = {"role": "verifier", "ref": "TASK-1", "parent_session_id": "parent"}
    result = launch_metadata("child", {}, worker_origin=origin)
    assert result["worker_origin"] == origin
    assert result["spawned_via"] == "watchtower"
    assert result["parent_session_id"] == "parent"
    assert launch_metadata("child", {}, worker_origin={"parent_session_id": "child"}).get("parent_session_id") is None
