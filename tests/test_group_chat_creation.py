"""Creation-time invariants for group chats."""

import json
import re
from pathlib import Path

import server


def test_group_chat_sidecar_exists_before_participant_is_injected(tmp_path, monkeypatch):
    """A participant can run its check-in as soon as injection returns."""
    chats = tmp_path / "group-chats"
    monkeypatch.setattr(server.os.path, "expanduser", lambda value: str(chats))
    monkeypatch.setattr(server.federation, "node_id", lambda: "test-node")
    monkeypatch.setattr(server, "_register_coordination", lambda _path: None)
    monkeypatch.setattr(server, "_group_chat_update_header_if_changed", lambda *_args, **_kwargs: None)

    observed_sidecars = []

    def inject_participant(_session_id, text):
        chat_path = Path(re.search(r'chat="([^"]+)"', text).group(1))
        sidecar_path = chat_path.with_suffix(".json")
        observed_sidecars.append(json.loads(sidecar_path.read_text(encoding="utf-8")))
        return {"ok": True}

    monkeypatch.setattr(server, "_inject_text_into_session", inject_participant)

    result = server._coordinate_sessions({
        "topic": "sidecar race",
        "session_ids": ["participant-1"],
        "include_human": False,
    })

    assert result["ok"] is True
    assert observed_sidecars[0]["session_ids"] == ["participant-1"]
