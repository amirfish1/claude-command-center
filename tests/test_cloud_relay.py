"""Pure-unit tests for ccc_server/cloud_relay.py (no server boot, no network).

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

from ccc_server import cloud_relay as cr


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
        "see /Users/alice/secret.txt and /home/bob/x, mail a@b.com, "
        "win C:\\Users\\z\\f.txt, tok deadbeefdeadbeef1234abcd0000, "
        "url https://user:pw@host.com/x")
    assert "/Users/alice" not in out
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


def test_validate_lower_seq_is_not_replay():
    # C1 fix: a below-high-water-mark seq on a NEW request_id is NOT a replay —
    # the relay legitimately re-queues an earlier undelivered command after a
    # later one advanced the mark. Rejecting it would drop a real user action.
    assert cr.validate_command(_env(seq=50), last_seq=50) is None
    assert cr.validate_command(_env(seq=49), last_seq=50) is None


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


def test_executor_lower_seq_still_executes(tmp_path):
    # C1 fix: an earlier command redelivered after a later one advanced the mark
    # must still execute exactly once, not be dropped as replay.
    api = CountingLocalAPI()
    ex, store = _executor(tmp_path, api)
    store.set_last_seq(100)
    res = ex.execute(_env(request_id="rp-1", seq=100))
    assert res["status"] == "ok"
    assert api.calls == 1


def test_executor_idempotency_dedup_runs_once(tmp_path):
    api = CountingLocalAPI()
    ex, _ = _executor(tmp_path, api)
    env = _env(request_id="dup-1", seq=200)
    a = ex.execute(env)
    b = ex.execute(env)  # duplicate request_id
    assert api.calls == 1           # body executed exactly once
    assert a == b                    # replay returns the recorded result
    assert a["status"] == "ok"


def test_executor_advances_last_seq_to_high_water_mark(tmp_path):
    api = CountingLocalAPI()
    ex, store = _executor(tmp_path, api)
    ex.execute(_env(request_id="s-1", seq=10))
    assert store.get_last_seq() == 10
    # A lower-seq new command executes but must not roll the mark backward.
    ex.execute(_env(request_id="s-2", seq=5))
    assert api.calls == 2
    assert store.get_last_seq() == 10


def test_executor_reserved_request_not_reexecuted(tmp_path):
    # H1 fix: a request_id reserved on a prior (crashed) attempt but never
    # finalized must resolve to `interrupted` WITHOUT re-dispatching — the
    # at-most-once guarantee for an action that sends input to an agent.
    api = CountingLocalAPI()
    ex, store = _executor(tmp_path, api)
    env = _env(request_id="crash-1", seq=7)
    store.reserve("crash-1")            # simulate: reserved, then crashed
    res = ex.execute(env)
    assert api.calls == 0               # NOT re-dispatched
    assert res["status"] == "error"
    assert res["error_code"] == "interrupted"
    # And it is now finalized: a further redelivery returns the recorded result.
    res2 = ex.execute(env)
    assert api.calls == 0
    assert res2 == res


def test_executor_reserves_before_dispatch(tmp_path):
    # The reservation is cleared once the command finalizes (normal path).
    api = CountingLocalAPI()
    ex, store = _executor(tmp_path, api)
    ex.execute(_env(request_id="ok-1", seq=3))
    assert api.calls == 1
    assert store.is_reserved("ok-1") is False
    assert store.get_result("ok-1")["status"] == "ok"


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


# ---------------------------------------------------------------------------
# v2 (rebased onto ccc_server/) hardening
# ---------------------------------------------------------------------------

def test_relay_url_requires_https_except_loopback():
    assert cr.validate_relay_url("https://relay.example.com/") == "https://relay.example.com"
    assert cr.validate_relay_url("http://127.0.0.1:8790") == "http://127.0.0.1:8790"
    assert cr.validate_relay_url("http://localhost:8790") == "http://localhost:8790"
    assert cr.validate_relay_url("http://relay.example.com") is None
    assert cr.validate_relay_url("https://user:pw@relay.example.com") is None
    assert cr.validate_relay_url("https://relay.example.com/?x=1") is None
    assert cr.validate_relay_url("ftp://relay.example.com") is None
    assert cr.validate_relay_url("") is None


def test_start_pairing_rejects_cleartext_relay(tmp_path):
    mgr = cr.CloudRelayManager(1, state_dir=str(tmp_path))
    r = mgr.start_pairing("ABCD1234", "http://relay.example.com")
    assert r == {"ok": False, "error": "relay_url_insecure"}


def test_reserve_is_atomic_and_refuses_finalized(tmp_path):
    store = cr.IdempotencyStore(str(tmp_path / "idem.sqlite3"))
    assert store.reserve("r-1") is True
    assert store.reserve("r-1") is False  # second racer loses
    store.record_result("r-1", {"request_id": "r-1", "status": "ok"})
    assert store.reserve("r-1") is False  # finalized ids never re-reserve


def test_executor_non_dict_envelope_rejected(tmp_path):
    store = cr.IdempotencyStore(str(tmp_path / "idem.sqlite3"))
    ex = cr.CommandExecutor(CountingLocalAPI(), store)
    assert ex.execute(["not", "a", "dict"])["status"] == "rejected"


def test_snapshot_reads_bounded_projected_cached_feeds():
    """Perf gate: one state push = exactly three loopback GETs, all against
    cached feeds, with the session list bounded + projected server-side."""
    seen = []

    class RecordingAPI(CountingLocalAPI):
        def get(self, path, query=None):
            seen.append((path, dict(query or {})))
            return super().get(path, query)

    cr.build_snapshot(RecordingAPI(), {"share_titles": True})
    paths = [p for p, _ in seen]
    assert paths == ["/api/sessions", "/api/attention", "/api/ux-fixes/health"]
    sq = seen[0][1]
    assert sq["all"] == "1" and sq["compact"] == "1"
    assert int(sq["limit"]) == cr.MAX_SESSIONS
    assert "jsonl_path" not in sq["fields"].split(",")
    assert seen[1][1]["scope"] == "recent"
    assert int(seen[1][1]["limit"]) == cr.MAX_ATTENTION


def test_context_pct_maps_live_context_percent():
    assert cr._context_pct({"live_context_percent": 41.6}) == 42
    assert cr._context_pct({"live_context_percent": 0}) is None
    assert cr._context_pct({"context_pct": 150}) == 100
    assert cr._context_pct({}) is None


def test_only_one_process_owns_the_loop(tmp_path):
    a = cr.CloudRelayManager(1, state_dir=str(tmp_path))
    b = cr.CloudRelayManager(1, state_dir=str(tmp_path))
    try:
        assert a._acquire_loop_lock() is True
        assert b._acquire_loop_lock() is False
        a._release_loop_lock()
        assert b._acquire_loop_lock() is True
    finally:
        a._release_loop_lock()
        b._release_loop_lock()


def _cmd(cap, rid, payload, seq=1):
    now = time.time()
    return {"v": 1, "request_id": rid, "seq": seq, "capability": cap,
            "created": cr._iso_from_epoch(now),
            "expires": cr._iso_from_epoch(now + 300), "payload": payload}


def test_question_answer_maps_relay_answer_dict(tmp_path):
    posts = []

    class API(CountingLocalAPI):
        def post(self, path, body=None):
            posts.append((path, body))
            return 200, {"ok": True}

    ex = cr.CommandExecutor(API(), cr.IdempotencyStore(str(tmp_path / "i.db")))
    r = ex.execute(_cmd("question.answer", "qa-1", {
        "session_ref": "sess-1", "question_id": "q1", "answer": {"index": 1}}))
    assert r["status"] == "ok"
    assert posts[0] == ("/api/answer-question",
                        {"session_id": "sess-1", "answers": [{"index": 1, "text": ""}]})


def test_session_history_bounded_and_redacted(tmp_path):
    import json as _json
    jsonl = tmp_path / "s.jsonl"
    with open(jsonl, "w") as f:
        for i in range(300):
            f.write(_json.dumps({"type": "user", "message": {
                "role": "user", "content": f"turn {i} /Users/alice/x " + "y" * 500}}) + "\n")

    class API(CountingLocalAPI):
        def get(self, path, query=None):
            if path == "/api/sessions":
                return 200, {"sessions": [{"id": "sess-1", "jsonl_path": str(jsonl)}]}
            return super().get(path, query)

    ex = cr.CommandExecutor(API(), cr.IdempotencyStore(str(tmp_path / "i.db")))
    r = ex.execute(_cmd("session.history", "h-1", {"session_ref": "sess-1"}))
    assert r["status"] == "ok"
    d = r["detail"]
    assert d["truncated"] is True
    assert cr._detail_json_bytes(d) <= cr.DETAIL_MAX_BYTES
    assert d["turns"][-1]["text"].startswith("turn 299")
    assert "/Users/alice" not in _json.dumps(d)


def test_session_history_unknown_session(tmp_path):
    ex = cr.CommandExecutor(CountingLocalAPI(sessions=[]),
                            cr.IdempotencyStore(str(tmp_path / "i.db")))
    r = ex.execute(_cmd("session.history", "h-2", {"session_ref": "nope"}))
    assert r["status"] == "error" and r["error_code"] == "session_not_found"


def test_inject_failure_carries_redacted_local_error(tmp_path):
    class API(CountingLocalAPI):
        def post(self, path, body=None):
            return 400, {"ok": False, "error": "cwd /Users/alice/gone missing",
                         "code": "invalid_cwd"}

    ex = cr.CommandExecutor(API(), cr.IdempotencyStore(str(tmp_path / "i.db")))
    r = ex.execute(_cmd("session.send_input", "e-1", {"session_ref": "s", "text": "hi"}))
    assert r["error_code"] == "invalid_cwd"
    assert "/Users/alice" not in r["detail"]["local_error"]
