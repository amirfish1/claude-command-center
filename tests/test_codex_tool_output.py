"""Complete tool results are available on demand without enlarging polls."""
import json

import pytest
import server
from ccc_server import codex_client, codex_live_events


@pytest.mark.parametrize("structured", [False, True])
def test_saved_full_output_is_lazy_and_checks_call_identity(tmp_path, monkeypatch, structured):
    text = "Brief section\n" * 300 + "END OF BRIEF"
    output = [{"type": "input_text", "text": text},
              {"type": "input_image", "image_url": "data:image/png;base64,AA=="}] if structured else text
    event = {"type": "response_item", "payload": {
        "type": "custom_tool_call_output" if structured else "function_call_output",
        "call_id": "call-1", "output": output}}
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(json.dumps(event) + "\n")
    monkeypatch.setattr(server, "_resolve_conversation_reader", lambda _: (transcript, server._parse_codex_event))
    preview = server._parse_codex_event(event, 1)
    assert preview["output_truncated"]
    assert len(preview["text"]) < 810
    assert server._conversation_tool_output("task", 1, "call-1") == text
    assert server._conversation_tool_output("task", 1, "other") is None
    assert server._conversation_tool_output("task", 2, "call-1") is None
    assert server._conversation_tool_output("task", "../rollout", "call-1") is None


def test_live_full_output_retains_scope_validation(monkeypatch):
    text = "Brief section\n" * 300 + "END OF BRIEF"
    item = {"id": "exec-1", "type": "commandExecution", "status": "completed", "aggregatedOutput": text}
    preview = codex_live_events._map_item("turn-1", item)[1]
    assert preview["output_truncated"]
    assert len(preview["text"]) < 810
    monkeypatch.setattr(codex_client.CODEX_CONVERSATIONS, "snapshot", lambda _: {
        "thread": {"id": "task", "cwd": "/example/repo", "turns": [{"id": "turn-1", "items": [item]}]}})
    validated = []
    monkeypatch.setattr(codex_client, "validate_operation_context", lambda *args, **kwargs: validated.append(args))
    data = {"context": {"thread_id": "task", "repo_path": "/example/repo"}, "turn_id": "turn-1", "item_id": "exec-1"}
    assert codex_client.codex_client_dispatch("tool-output", data) == {"ok": True, "output": text.strip()}
    assert validated
    assert not codex_client.codex_client_dispatch("tool-output", {**data, "item_id": "other"})["ok"]
    def reject(*args, **kwargs):
        raise ValueError("Repository mismatch")
    monkeypatch.setattr(codex_client, "validate_operation_context", reject)
    assert not codex_client.codex_client_dispatch("tool-output", data)["ok"]
