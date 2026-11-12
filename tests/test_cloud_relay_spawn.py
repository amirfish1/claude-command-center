"""Remote creation must remain local, bounded, and at most once."""
import time

import pytest

from ccc_server import cloud_relay as cr


def envelope(payload, request_id="spawn-1"):
    return {"v": 1, "capability": "session.spawn", "seq": 1,
            "request_id": request_id,
            "expires": cr._iso_from_epoch(time.time() + 300),
            "payload": payload,
            "issued_by": {"type": "agent", "name": "Test agent"}}


class Local:
    def __init__(self, repo):
        self.row = {"id": "source", "session_cwd": str(repo),
                    "engine": "claude", "model": "test-model"}
        self.posts = []
        self.response = (200, {"ok": True, "session_id": "new-session",
                               "spawn_id": "new-spawn", "engine": "claude",
                               "repo_path": str(repo), "log": str(repo / "log")})

    def get(self, endpoint, query=None):
        assert endpoint == "/api/sessions"
        assert "session_cwd" in query["fields"]
        return 200, {"sessions": [self.row]}

    def post(self, endpoint, body):
        self.posts.append((endpoint, body))
        return self.response


def executor(tmp_path):
    api = Local(tmp_path)
    return cr.CommandExecutor(api, cr.IdempotencyStore(str(tmp_path / "i.db"))), api


def test_spawn_fresh_session_and_retry_receipt(tmp_path):
    ex, api = executor(tmp_path)
    env = envelope({"session_ref": "source", "prompt": "Check the build"})
    result = ex.execute(env)
    assert result["status"] == "ok"
    assert result["detail"]["session_id"] == "new-session"
    assert "repo_path" not in result["detail"] and "log" not in result["detail"]
    assert ex.execute(env) == result
    assert len(api.posts) == 1
    endpoint, body = api.posts[0]
    assert endpoint == "/api/sessions/spawn"
    assert body["repo_path"] == str(tmp_path)
    assert body["task_key"] == "cloud-spawn-spawn-1"
    assert body["prompt"].startswith("[From Test agent via CCC Cloud, request spawn-1]\n")
    assert body["prompt"].endswith("Check the build")
    assert body["model"] == "test-model"
    assert "session_id" not in body and "resume" not in body
    assert "parent_session_id" not in body


@pytest.mark.parametrize("extra", [
    {"repo_path": "/tmp"}, {"cwd": "/tmp"}, {"node": "peer"},
    {"env": {}}, {"confirm_blocked_model": True}, {"prompt": "  "},
    {"engine": ["claude"]}, {"model": "bad\nmodel"}, {"name": " name"},
    {"session_ref": 123}, {"reasoning_effort": ""},
])
def test_spawn_rejects_invalid_payload_before_local_call(tmp_path, extra):
    ex, api = executor(tmp_path)
    payload = dict({"session_ref": "source", "prompt": "Check"}, **extra)
    assert ex.execute(envelope(payload))["error_code"] == "invalid_payload"
    assert api.posts == []


def test_spawn_checks_utf8_byte_limit(tmp_path):
    ex, api = executor(tmp_path)
    result = ex.execute(envelope({"session_ref": "source", "prompt": "é" * 9000}))
    assert result["error_code"] == "payload_too_large"
    assert api.posts == []


@pytest.mark.parametrize("row", [None, {"id": "source"},
                                 {"id": "source", "repo_path": "repo-slug"}])
def test_spawn_requires_local_source_with_resolvable_directory(tmp_path, row):
    ex, api = executor(tmp_path)
    api.row = row
    assert ex.execute(envelope({"session_ref": "source", "prompt": "Check"}))["status"] == "error"
    assert api.posts == []


def test_spawn_changing_engine_does_not_inherit_source_model(tmp_path):
    ex, api = executor(tmp_path)
    ex.execute(envelope({"session_ref": "source", "prompt": "Check", "engine": "codex"}))
    assert api.posts[0][1]["engine"] == "codex"
    assert "model" not in api.posts[0][1]


@pytest.mark.parametrize("response", [(500, {"ok": False}), (200, {"ok": True})])
def test_spawn_never_claims_success_without_owner_confirmation(tmp_path, response):
    ex, api = executor(tmp_path)
    api.response = response
    env = envelope({"session_ref": "source", "prompt": "Check"})
    assert ex.execute(env)["status"] == "error"
    ex.execute(env)
    assert len(api.posts) == 1


def test_spawn_pending_session_returns_spawn_id(tmp_path):
    ex, api = executor(tmp_path)
    api.response = (200, {"ok": True, "spawn_id": "starting"})
    result = ex.execute(envelope({"session_ref": "source", "prompt": "Check"}))
    assert result["detail"] == {"spawn_id": "starting", "session_id_pending": True}


def test_spawn_preserves_engine_handles_and_provider_model_ids(tmp_path):
    ex, api = executor(tmp_path)
    api.response = (200, {"ok": True, "session_id": "devincli-example-session",
                          "spawn_id": "devin-acp-example-session",
                          "model": "provider/vendor/model-name"})
    detail = ex.execute(envelope({"session_ref": "source", "prompt": "Check"}))["detail"]
    assert detail["session_id"] == "devincli-example-session"
    assert detail["spawn_id"] == "devin-acp-example-session"
    assert detail["model"] == "provider/vendor/model-name"


def test_spawn_is_advertised_in_pairing_and_snapshots():
    assert "session_spawn" in cr.DEVICE_CAPABILITIES

    class EmptyAPI:
        def get(self, endpoint, query=None):
            return 200, {}

    snapshot = cr.build_snapshot(EmptyAPI(), dict(cr._CONFIG_DEFAULTS))
    assert "session_spawn" in snapshot["device"]["capabilities"]
