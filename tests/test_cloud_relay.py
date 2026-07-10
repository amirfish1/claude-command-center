"""Pure-unit tests for cloud_relay.py (no server boot, no network).

Covers the security-critical local-client behaviour: redaction, command
validation order (expiry / seq replay / idempotency dedup), snapshot
minimization + caps, 0600 credentials, and the CCC_CLOUD_DISABLED gate.
"""
import os
import stat
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import cloud_relay as cr


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class CountingLocalAPI:
    """Loopback stand-in that counts write dispatches so idempotency can be
    proven to execute the body exactly once."""

    def __init__(self, sessions=None):
        self.calls = 0
        self.sessions = sessions if sessions is not None else [{
            "id": "sess-1",
            "display_name": "Fix /Users/alice/proj login bug",
            "engine": "claude", "is_live": True, "mtime": time.time(),
            "state": "waiting", "question_waiting": True,
            "question_text": "Deploy at /home/bob/x? tok abcdef0123456789abcd0000",
            "question_options": ["Yes", "No"], "folder_label": "secret-repo",
            "jsonl_path": "/nonexistent/sess-1.jsonl",
        }]

    def get(self, path, query=None):
        if path == "/api/sessions":
            return 200, {"ok": True, "sessions": self.sessions}
        if path == "/api/attention":
            return 200, {"ok": True, "items": [{
                "kind": "soft_block", "priority": 1, "session_id": "sess-1",
                "question_text": "ping me at bob@example.com about /Users/x",
                "mtime": time.time(),
            }]}
        if path == "/api/ux-fixes/health":
            return 200, {"ok": True, "queues": [{
                "queue": "UX", "depth": 2, "total": 5, "workers": 1,
                "state": "draining", "oldest_open_age_seconds": 900,
            }]}
        return 200, {"ok": True}

    def post(self, path, body=None):
        self.calls += 1
        return 200, {"ok": True}

    def call(self, method, path, body=None, query=None, timeout=None):
        return self.get(path, query) if method == "GET" else self.post(path, body)


def _env(cap="session.send_input", seq=1, request_id="r-1", expires_delta=300,
         session_ref="sess-1", text="hello"):
    now = time.time()
    return {
        "v": 1, "request_id": request_id, "capability": cap,
        "created": cr._iso_from_epoch(now),
        "expires": cr._iso_from_epoch(now + expires_delta),
        "seq": seq, "idempotency_key": request_id,
        "payload": {"session_ref": session_ref, "text": text},
    }


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

def test_redact_strips_paths_emails_tokens():
    out = cr.redact(
        "see /Users/amir/secret.txt and /home/bob/x, mail a@b.com, "
        "win C:\\Users\\z\\f.txt, tok deadbeefdeadbeef1234abcd0000, "
        "url https://user:pw@host.com/x")
    assert "/Users/amir" not in out
    assert "/home/bob" not in out
    assert "a@b.com" not in out
    assert "C:\\Users\\z" not in out
    assert "deadbeefdeadbeef1234abcd0000" not in out
    assert "user:pw@host.com" not in out
    assert "[redacted]" in out


def test_redact_keeps_plain_words():
    out = cr.redact("deploy the login page please")
    assert out == "deploy the login page please"


def test_truncation_caps():
    assert len(cr.redact_cap("x" * 999, cr.CAP_TITLE)) <= cr.CAP_TITLE
    assert len(cr.redact_cap("y" * 999, cr.CAP_SUMMARY)) <= cr.CAP_SUMMARY
    assert len(cr.redact_cap("z" * 9999, cr.CAP_QUESTION)) <= cr.CAP_QUESTION


# ---------------------------------------------------------------------------
# Validation order
# ---------------------------------------------------------------------------

def test_validate_expired():
    assert cr.validate_command(_env(expires_delta=-120), last_seq=0) == "expired"


def test_validate_seq_replay():
    # New request_id but a seq that is not strictly greater than last_seq.
    assert cr.validate_command(_env(seq=50), last_seq=50) == "replay"
    assert cr.validate_command(_env(seq=49), last_seq=50) == "replay"


def test_validate_ok_and_unknown_capability():
    assert cr.validate_command(_env(seq=51), last_seq=50) is None
    assert cr.validate_command(_env(cap="session.stop"), last_seq=0) == "unknown_capability"


def test_validate_bad_version_and_payload():
    e = _env()
    e["v"] = 2
    assert cr.validate_command(e, last_seq=0) == "invalid_payload"
    e2 = _env()
    e2["payload"] = {"text": "hi"}  # missing session_ref
    assert cr.validate_command(e2, last_seq=0) == "invalid_payload"


def test_validate_text_too_large():
    big = "a" * (cr.COMMAND_TEXT_MAX_BYTES + 1)
    assert cr.validate_command(_env(text=big), last_seq=0) == "payload_too_large"


# ---------------------------------------------------------------------------
# Executor: expiry, replay, idempotency dedup
# ---------------------------------------------------------------------------

def _executor(tmp_path, api):
    paths = cr.CloudPaths(str(tmp_path))
    paths.ensure()
    store = cr.IdempotencyStore(paths.idempotency)
    return cr.CommandExecutor(api, store, paths), store


def test_executor_expired_status(tmp_path):
    ex, _ = _executor(tmp_path, CountingLocalAPI())
    res = ex.execute(_env(request_id="e-1", seq=5, expires_delta=-500))
    assert res["status"] == "expired"
    assert res["error_code"] == "expired"


def test_executor_seq_replay_status(tmp_path):
    ex, store = _executor(tmp_path, CountingLocalAPI())
    store.set_last_seq(100)
    res = ex.execute(_env(request_id="rp-1", seq=100))
    assert res["status"] == "rejected"
    assert res["error_code"] == "replay"


def test_executor_idempotency_dedup_runs_once(tmp_path):
    api = CountingLocalAPI()
    ex, _ = _executor(tmp_path, api)
    env = _env(request_id="dup-1", seq=200)
    a = ex.execute(env)
    b = ex.execute(env)  # duplicate request_id
    assert api.calls == 1           # body executed exactly once
    assert a == b                    # replay returns the recorded result
    assert a["status"] == "ok"


def test_executor_advances_last_seq_only_on_execute(tmp_path):
    api = CountingLocalAPI()
    ex, store = _executor(tmp_path, api)
    ex.execute(_env(request_id="s-1", seq=10))
    assert store.get_last_seq() == 10
    # A stale-seq new command is rejected and must NOT roll back / advance seq.
    ex.execute(_env(request_id="s-2", seq=5))
    assert store.get_last_seq() == 10


# ---------------------------------------------------------------------------
# Snapshot minimization + caps
# ---------------------------------------------------------------------------

def test_snapshot_share_titles_on_redacts():
    cfg = dict(cr._CONFIG_DEFAULTS, share_titles=True)
    snap = cr.build_snapshot(CountingLocalAPI(), cfg)
    s0 = snap["sessions"][0]
    assert s0["title_source"] == "redacted"
    assert "/Users/alice" not in s0["title"]
    assert "repo_label" in s0
    assert "abcdef0123456789abcd0000" not in snap["questions"][0]["text"]
    assert "bob@example.com" not in snap["attention"][0]["summary"]


def test_snapshot_share_titles_off_opaque():
    cfg = dict(cr._CONFIG_DEFAULTS, share_titles=False)
    snap = cr.build_snapshot(CountingLocalAPI(), cfg)
    s0 = snap["sessions"][0]
    assert s0["title_source"] == "opaque"
    assert s0["title"].startswith("Session on ")
    assert "repo_label" not in s0
    # An opaque snapshot must never leak the repo folder label anywhere.
    assert "secret-repo" not in repr(snap)


def test_snapshot_caps_enforced():
    many = []
    for i in range(300):
        many.append({
            "id": f"s{i}", "display_name": "x" * 500, "engine": "claude",
            "is_live": True, "mtime": time.time(), "state": "idle",
            "question_waiting": False, "folder_label": "r" * 500,
        })
    cfg = dict(cr._CONFIG_DEFAULTS, share_titles=True)
    snap = cr.build_snapshot(CountingLocalAPI(sessions=many), cfg)
    assert len(snap["sessions"]) <= cr.MAX_SESSIONS
    assert all(len(s["title"]) <= cr.CAP_TITLE for s in snap["sessions"])


def test_snapshot_capabilities_and_schedules_shape():
    snap = cr.build_snapshot(CountingLocalAPI(), dict(cr._CONFIG_DEFAULTS))
    caps = snap["device"]["capabilities"]
    for required in ("sessions", "attention", "workers", "questions",
                     "session_input", "question_answer", "session_wake"):
        assert required in caps
    # schedules is present in caps iff the snapshot carries a schedules list.
    assert ("schedules" in caps) == ("schedules" in snap)


# ---------------------------------------------------------------------------
# Credentials 0600
# ---------------------------------------------------------------------------

def test_credentials_written_0600(tmp_path):
    paths = cr.CloudPaths(str(tmp_path))
    cr.save_credentials(paths, {"device_id": "dev-1", "device_secret": "s3cr3t"})
    mode = stat.S_IMODE(os.stat(paths.credentials).st_mode)
    assert mode == 0o600
    loaded = cr.load_credentials(paths)
    assert loaded["device_id"] == "dev-1"
    assert loaded["device_secret"] == "s3cr3t"


def test_config_does_not_hold_secret(tmp_path):
    paths = cr.CloudPaths(str(tmp_path))
    cfg = dict(cr._CONFIG_DEFAULTS, device_id="dev-1", enabled=True)
    cr.save_config(paths, cfg)
    with open(paths.config) as f:
        raw = f.read()
    assert "device_secret" not in raw


# ---------------------------------------------------------------------------
# CCC_CLOUD_DISABLED gate
# ---------------------------------------------------------------------------

def test_cloud_disabled_env_gate(monkeypatch):
    monkeypatch.delenv("CCC_CLOUD_DISABLED", raising=False)
    assert cr.cloud_disabled_env() is False
    for truthy in ("1", "true", "yes", "on", "ON", "Yes"):
        monkeypatch.setenv("CCC_CLOUD_DISABLED", truthy)
        assert cr.cloud_disabled_env() is True
    monkeypatch.setenv("CCC_CLOUD_DISABLED", "0")
    assert cr.cloud_disabled_env() is False


def test_manager_enable_without_pairing_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("CCC_CLOUD_DISABLED", raising=False)
    mgr = cr.CloudRelayManager(8090, ccc_version="9.9.9", state_dir=str(tmp_path))
    res = mgr.set_config(enabled=True)
    assert res["ok"] is False
    assert res["error"] == "not_paired"
    # maybe_start must be a no-op when unpaired.
    assert mgr.maybe_start() is False
