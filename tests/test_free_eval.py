"""Tests for ccc_server.free_eval (L05 free-model ranking).

A fake freellmapi router on a real loopback socket exercises the whole path:
catalog fetch, the five-task agent loop over /v1/messages, scoring,
persistence, and the anthropic-map pin. The fake models each get a scripted
policy (a champion that uses tools correctly, a chatterbox that only talks,
a slowpoke that sleeps) so ranking is deterministic.
"""
import http.server
import json
import threading
import time
from pathlib import Path

import pytest

from ccc_server import free_eval


# ---------------------------------------------------------------------------
# Fake router
# ---------------------------------------------------------------------------

FAKE_KEY = "ccc-test-unified-key"
ADMIN = {"email": "admin@ccc.test", "password": "test-password-123"}

CATALOG = [
    {"id": "champ-7b", "name": "Champ 7B", "owned_by": "groq",
     "context_window": 131072, "context_length": 131072, "available": True,
     "execution_status": "ready", "supported_parameters": ["tools", "temperature"]},
    {"id": "chatter-3b", "name": "Chatter 3B", "owned_by": "kilo",
     "context_window": 32768, "context_length": 32768, "available": True,
     "execution_status": "ready", "supported_parameters": ["temperature"]},
    {"id": "slowpoke-70b", "name": "Slowpoke 70B", "owned_by": "nvidia",
     "context_window": 131072, "context_length": 131072, "available": True,
     "execution_status": "ready", "supported_parameters": ["tools"]},
    {"id": "locked-13b", "name": "Locked 13B", "owned_by": "mistral",
     "context_window": 32768, "context_length": 32768, "available": False,
     "execution_status": "needsKey", "supported_parameters": ["tools"]},
    {"id": "auto", "name": "Auto", "owned_by": "freellmapi", "available": True},
    {"id": "claude-sonnet-4-5", "name": "Claude alias", "owned_by": "freellmapi",
     "available": True},
]


def _champion_policy(body, tools_available):
    """A competent small model: does what each task prompt literally asks."""
    messages = body["messages"]
    last = messages[-1]
    # After a tool_result, declare done (or issue the next call for the
    # multi-step task, which needs two reads before the write).
    if isinstance(last.get("content"), list):
        results = last["content"]
        texts = [r.get("content", "") for r in results if isinstance(r, dict)]
        joined = "||".join(texts)
        if "codeword is plum" in joined:
            return _tool("call_ans", "write_file",
                         {"path": "answer.txt", "content": "plum"})
        if "19" in joined and "23" in joined and "a.txt" not in joined:
            return _tool("call_sum", "write_file",
                         {"path": "sum.txt", "content": "42"})
        return {"content": [{"type": "text", "text": "Done."}],
                "stop_reason": "end_turn"}

    prompt = last.get("content", "")
    if not isinstance(prompt, str):
        prompt = ""
    if "greet.py" in prompt:
        content = ('def greet():\n    return "Hello, world!"\n\n'
                   'if __name__ == "__main__":\n    print(greet())\n')
        return _tool("call_greet", "write_file",
                     {"path": "greet.py", "content": content})
    if "test_calc.py" in prompt:
        content = ("def add(a, b):\n    return a + b\n\n"
                   "def subtract(a, b):\n    return a - b\n")
        return _tool("call_calc", "write_file",
                     {"path": "calc.py", "content": content})
    if "string_tools.py" in prompt:
        content = ('def shout(text):\n    return text.upper() + "!"\n\n'
                   "def whisper(text):\n    return text.lower()\n")
        return _tool("call_tools", "write_file",
                     {"path": "string_tools.py", "content": content})
    if "notes.txt" in prompt:
        return _tool("call_read", "read_file", {"path": "data/notes.txt"})
    if "a.txt and b.txt" in prompt:
        # First turn: read both files (two calls), then the policy writes
        # the sum on the next turn.
        return {"content": [
            {"type": "tool_use", "id": "call_a", "name": "read_file",
             "input": {"path": "a.txt"}},
            {"type": "tool_use", "id": "call_b", "name": "read_file",
             "input": {"path": "b.txt"}},
        ], "stop_reason": "tool_use"}
    return {"content": [{"type": "text", "text": "Not sure."}],
            "stop_reason": "end_turn"}


def _tool(call_id, name, inputs):
    return {"content": [
        {"type": "tool_use", "id": call_id, "name": name, "input": inputs}
    ], "stop_reason": "tool_use"}


def _chatter_policy(body, _tools):
    """A model that only ever talks — never emits a tool call."""
    return {"content": [{"type": "text",
                         "text": "I would write the file now. Trust me."}],
            "stop_reason": "end_turn"}


def _sleepy_champion(champion):
    def policy(body, tools):
        time.sleep(0.35)
        return champion(body, tools)
    return policy


POLICIES = {
    "champ-7b": _champion_policy,
    "chatter-3b": _chatter_policy,
    "slowpoke-70b": _sleepy_champion(_champion_policy),
}


class FakeRouter(http.server.BaseHTTPRequestHandler):
    """Speaks just enough freellmapi to host a full eval."""

    anthropic_map = {}
    requests_seen = []

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self):
        token = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        return token in (FAKE_KEY, "admin-token") or \
            self.headers.get("x-api-key") == FAKE_KEY

    def log_message(self, *_args):
        pass

    def do_GET(self):  # noqa: N802
        FakeRouter.requests_seen.append(("GET", self.path))
        if self.path == "/api/ping":
            self._send(200, {"ok": True})
            return
        if self.path == "/v1/models":
            if not self._auth_ok():
                self._send(401, {"error": {"message": "Invalid API key"}})
                return
            self._send(200, {"object": "list", "data": CATALOG})
            return
        if self.path == "/api/settings/api-key":
            if not self._auth_ok():
                self._send(401, {"error": {"message": "no"}})
                return
            self._send(200, {"apiKey": FAKE_KEY})
            return
        self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        FakeRouter.requests_seen.append(("POST", self.path))
        if self.path == "/api/auth/login":
            if body.get("email") == ADMIN["email"] and \
                    body.get("password") == ADMIN["password"]:
                self._send(200, {"token": "admin-token", "email": ADMIN["email"]})
            else:
                self._send(401, {"error": {"message": "Invalid email or password"}})
            return
        if self.path == "/v1/messages":
            if not self._auth_ok():
                self._send(401, {"type": "error", "error": {
                    "type": "authentication_error", "message": "Invalid API key"}})
                return
            model = body.get("model") or ""
            policy = POLICIES.get(model)
            if policy is None:
                self._send(404, {"type": "error", "error": {
                    "type": "not_found_error", "message": f"no such model {model}"}})
                return
            reply = policy(body, body.get("tools"))
            reply.setdefault("id", "msg_fake")
            reply.setdefault("type", "message")
            reply.setdefault("role", "assistant")
            reply.setdefault("model", model)
            reply.setdefault("usage", {"input_tokens": 10, "output_tokens": 5})
            self._send(200, reply)
            return
        self._send(404, {"error": {"message": "not found"}})

    def do_PUT(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        FakeRouter.requests_seen.append(("PUT", self.path))
        if self.path == "/api/settings/anthropic-map":
            if not self._auth_ok():
                self._send(401, {"error": {"message": "no"}})
                return
            FakeRouter.anthropic_map.update(body)
            self._send(200, {"map": dict(FakeRouter.anthropic_map)})
            return
        self._send(404, {"error": {"message": "not found"}})


@pytest.fixture()
def fake_router(tmp_path, monkeypatch):
    """Fake router + env pointing free_eval at it, with a fresh state file."""
    FakeRouter.anthropic_map = {}
    FakeRouter.requests_seen = []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeRouter)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    monkeypatch.setenv("CCC_FREE_ROUTER_URL", base)
    monkeypatch.setenv("CCC_FREE_ROUTER_KEY", FAKE_KEY)
    monkeypatch.setenv("CCC_FREE_ROUTER_ADMIN_EMAIL", ADMIN["email"])
    monkeypatch.setenv("CCC_FREE_ROUTER_ADMIN_PASSWORD", ADMIN["password"])
    monkeypatch.setenv("CCC_FREE_ROUTER_STATE", str(tmp_path / "free-router.json"))
    monkeypatch.setenv("CCC_FREE_EVAL_STATE", str(tmp_path / "free-eval.json"))
    monkeypatch.setenv("CCC_FREE_EVAL_MAX_MODELS", "10")
    with free_eval._CONFIG_LOCK:
        free_eval._CONFIG_CACHE.update({"ts": 0.0, "cfg": "unset"})
    with free_eval._CATALOG_LOCK:
        free_eval._CATALOG_CACHE.update(
            {"key": None, "ts": 0.0, "models": [], "error": None})
    with free_eval._STORE_LOCK:
        free_eval._STORE_CACHE.update({"sig": None, "data": None})
    try:
        yield base
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def wait_for_job(job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = free_eval.job_status(job_id)
        if status and status["status"] in ("done", "error"):
            return status
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} never finished")


# ---------------------------------------------------------------------------
# Unit tests: tasks, tools, scoring
# ---------------------------------------------------------------------------

def test_every_task_starts_failing(tmp_path):
    """A task that passes before the model touches it is a broken fixture."""
    for task in free_eval.eval_tasks():
        task["setup"](tmp_path)
        ok, _detail = task["check"](tmp_path)
        assert not ok, f"{task['id']} passes without any model work"


def test_write_file_tool_respects_sandbox(tmp_path):
    task = free_eval.eval_tasks()[0]
    out = free_eval._exec_tool(task, tmp_path, "write_file",
                               {"path": "../escape.txt", "content": "x"})
    assert out.startswith("error")
    assert not (tmp_path.parent / "escape.txt").exists()


def test_tools_gate_and_score_shape():
    tasks = [{"passed": True, "median_request_ms": 800},
             {"passed": True, "median_request_ms": 900},
             {"passed": False, "median_request_ms": 1000},
             {"passed": True, "median_request_ms": 700},
             {"passed": True, "median_request_ms": 900}]
    agg = free_eval._score_model(tasks)
    assert agg["passed"] == 4
    assert agg["tasks"] == 5
    assert agg["pass_rate"] == pytest.approx(0.8)
    assert 60 < agg["score"] < 100
    assert agg["median_request_ms"] == 900


def test_slow_models_lose_speed_points():
    fast = [{"passed": True, "median_request_ms": 1000}] * 5
    slow = [{"passed": True, "median_request_ms": 25000}] * 5
    assert free_eval._score_model(fast)["score"] == 100.0
    slow_score = free_eval._score_model(slow)["score"]
    assert slow_score < 85


# ---------------------------------------------------------------------------
# HTTP integration: catalog, full eval, pinning
# ---------------------------------------------------------------------------

def test_catalog_requests_rich_openai_metadata(monkeypatch):
    captured = {}
    def request(method, url, **kwargs):
        captured.update(kwargs["headers"])
        return 200, {"object": "list", "data": CATALOG}, None
    monkeypatch.setattr(free_eval, "_http_json", request)
    rows, err = free_eval.fetch_catalog({"base_url": "http://127.0.0.1:3017", "unified_key": FAKE_KEY})
    assert err is None
    assert "anthropic-version" not in {key.lower() for key in captured}
    assert captured["Authorization"] == "Bearer " + FAKE_KEY
    assert next(row for row in rows if row["id"] == "champ-7b")["ready"] is True


def test_catalog_filters_virtual_entries(fake_router):
    cfg = free_eval.router_config(force=True)
    assert cfg["base_url"] == fake_router
    assert cfg["unified_key"] == FAKE_KEY
    rows, err = free_eval.fetch_catalog(cfg)
    assert err is None
    ids = {r["id"] for r in rows}
    assert ids == {"champ-7b", "chatter-3b", "slowpoke-70b", "locked-13b"}
    by_id = {r["id"]: r for r in rows}
    assert by_id["champ-7b"]["supports_tools"] is True
    assert by_id["chatter-3b"]["supports_tools"] is False
    assert by_id["champ-7b"]["ready"] is True
    assert by_id["locked-13b"]["ready"] is False
    assert by_id["champ-7b"]["platform"] == "groq"
    assert by_id["champ-7b"]["context"] == 131072


def test_catalog_cached_by_ttl(fake_router):
    cfg = free_eval.router_config(force=True)
    before = len([r for r in FakeRouter.requests_seen if r == ("GET", "/v1/models")])
    free_eval.catalog_cached(cfg)
    free_eval.catalog_cached(cfg)
    free_eval.catalog_cached(cfg)
    after = len([r for r in FakeRouter.requests_seen if r == ("GET", "/v1/models")])
    assert after - before == 1, "catalog fetch must be TTL-cached, not per poll"


def test_full_eval_ranks_champion_and_pins_it(fake_router, monkeypatch):
    payload, status = free_eval.start_eval()
    assert status == 200
    job = wait_for_job(payload["job_id"], timeout=120)
    assert job["status"] == "done", job.get("error")
    assert job["progress"] == 1.0

    best = job["result"]["best"]
    assert best["model"] == "champ-7b"
    assert best["pinned"] is True
    # anthropic-map PUT went to the fake router with the winner.
    puts = [r for r in FakeRouter.requests_seen
            if r == ("PUT", "/api/settings/anthropic-map")]
    assert puts, "winner was never pinned through anthropic-map"
    assert FakeRouter.anthropic_map["default"] == "champ-7b"
    assert FakeRouter.anthropic_map["sonnet"] == "champ-7b"

    rows = free_eval.models_payload()
    by_id = {r["id"]: r for r in rows}
    champ = by_id["champ-7b"]
    assert champ["rank"] == 1
    assert champ["score"] > 0
    assert champ["passed"] == 5
    assert champ["evaluated"] is True
    assert champ["best"] is True
    assert champ["pinned"] is True
    assert champ["supports_tools"] is True
    chatter = by_id["chatter-3b"]
    assert chatter["evaluated"] is True
    assert chatter["passed"] == 0
    assert chatter["rank"] and chatter["rank"] > champ["rank"]

    # The non-tool chatterbox must still record an honest verdict: it
    # failed because it cannot drive tools, not because the harness broke.
    store = free_eval._load_store()
    chatter_store = store["models"]["chatter-3b"]
    assert all(not t["used_tools"] for t in chatter_store["per_task"])

    # Contract keys all present on every row.
    for row in rows:
        for key in ("id", "platform", "supports_tools", "context",
                    "rank", "score", "ready"):
            assert key in row


def test_eval_persists_to_state_file(fake_router, tmp_path):
    payload, _status = free_eval.start_eval(["champ-7b"])
    wait_for_job(payload["job_id"], timeout=120)
    state_file = Path(tmp_path / "free-eval.json")
    assert state_file.is_file()
    data = json.loads(state_file.read_text())
    assert "champ-7b" in data["models"]
    assert data["best"]["model"] == "champ-7b"
    assert data["best"]["pinned"] is True
    # Never leak credentials into the persisted leaderboard.
    blob = state_file.read_text()
    assert FAKE_KEY not in blob
    assert ADMIN["password"] not in blob


def test_selected_models_only_race(fake_router):
    payload, _status = free_eval.start_eval(["chatter-3b"])
    job = wait_for_job(payload["job_id"], timeout=60)
    assert job["status"] == "done"
    store = free_eval._load_store()
    assert set(store["models"]) == {"chatter-3b"}
    # chatter never wins a pin: zero passes, no tools observed.
    assert store["best"]["model"] is None or not store["best"]["pinned"]


def test_prefer_endpoint_pins_requested_model(fake_router):
    payload, status = free_eval.prefer_model("champ-7b")
    assert status == 200
    assert payload["ok"] is True
    assert FakeRouter.anthropic_map["default"] == "champ-7b"
    payload, status = free_eval.prefer_model("ghost-model")
    assert status == 404
    assert payload["ok"] is False


def test_second_eval_refuses_while_running(fake_router, monkeypatch):
    """Only one race at a time; the second gets the running job_id back."""

    def sleepy(body, tools):
        time.sleep(1.2)
        return _champion_policy(body, tools)

    monkeypatch.setitem(POLICIES, "champ-7b", sleepy)
    first, status = free_eval.start_eval(["champ-7b"])
    assert status == 200
    second, status2 = free_eval.start_eval()
    assert status2 == 409
    assert second["job_id"] == first["job_id"]
    wait_for_job(first["job_id"], timeout=120)


def test_no_router_degrades(tmp_path, monkeypatch):
    """No env, no state file, nothing on the probe port -> [] and 503."""
    monkeypatch.delenv("CCC_FREE_ROUTER_URL", raising=False)
    monkeypatch.delenv("CCC_FREE_ROUTER_KEY", raising=False)
    monkeypatch.delenv("CCC_FREE_ROUTER_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("CCC_FREE_ROUTER_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("CCC_FREE_ROUTER_STATE", str(tmp_path / "nope.json"))
    monkeypatch.setenv("CCC_FREE_EVAL_STATE", str(tmp_path / "eval.json"))
    monkeypatch.setattr(free_eval, "DEFAULT_ROUTER_PORT", 1)
    monkeypatch.setattr(free_eval, "_module_router_status", lambda: None)
    monkeypatch.setattr(free_eval, "_module_secret", lambda *a: "")
    with free_eval._CONFIG_LOCK:
        free_eval._CONFIG_CACHE.update({"ts": 0.0, "cfg": "unset"})

    assert free_eval.router_config(probe=True, force=True) is None
    assert free_eval.models_payload() == []
    payload, status = free_eval.start_eval()
    assert status == 503
    assert payload["ok"] is False
    info = free_eval.eval_info()
    assert info["router_configured"] is False
    assert info["running_job"] is None


def test_eval_state_file_permissions(fake_router, tmp_path):
    payload, _ = free_eval.start_eval(["champ-7b"])
    wait_for_job(payload["job_id"], timeout=120)
    mode = (Path(tmp_path) / "free-eval.json").stat().st_mode & 0o777
    assert mode == 0o600


# ---------------------------------------------------------------------------
# Weekly leaderboard job (scripts/leaderboard-weekly.py)
# ---------------------------------------------------------------------------

def _weekly():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "scripts" / "leaderboard-weekly.py"
    spec = importlib.util.spec_from_file_location("leaderboard_weekly", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_weekly_run_stages_page_without_pinning(fake_router, tmp_path):
    weekly = _weekly()
    staging = tmp_path / "staging"
    assert weekly.run(staging, ["champ-7b", "chatter-3b"], log=lambda line: None) == 0

    data = json.loads((staging / "data.json").read_text())
    assert [m["id"] for m in data["models"]] == ["champ-7b", "chatter-3b"]
    assert data["best"] == "champ-7b"
    assert (staging / "index.html").is_file()
    # Unattended runs never change the router's default model.
    assert ("PUT", "/api/settings/anthropic-map") not in FakeRouter.requests_seen
    assert FakeRouter.anthropic_map == {}
    status = json.loads((staging / "last-run.json").read_text())
    assert status["status"] == "ok" and status["public_models"] == 2
    for staged in staging.iterdir():
        assert FAKE_KEY not in staged.read_text()
        assert ADMIN["password"] not in staged.read_text()
    ok, message = weekly.health(staging, 8)
    assert ok, message


def test_weekly_eval_error_is_recorded(fake_router, tmp_path, monkeypatch):
    monkeypatch.setattr(free_eval, "fetch_catalog", lambda cfg: ([], "the router is down"))
    weekly = _weekly()
    staging = tmp_path / "staging"
    assert weekly.run(staging, log=lambda line: None) == weekly.EXIT_FAILED
    status = json.loads((staging / "last-run.json").read_text())
    assert status["status"] == "eval_error" and status["step"] == "catalog"
    assert not (staging / "data.json").exists()
    ok, message = weekly.health(staging, 8)
    assert not ok and "the router is down" in message


def test_weekly_without_router_records_no_router(tmp_path, monkeypatch):
    monkeypatch.setattr(free_eval, "router_config", lambda probe=True, force=False: None)
    weekly = _weekly()
    staging = tmp_path / "staging"
    assert weekly.run(staging, log=lambda line: None) == weekly.EXIT_NO_ROUTER
    ok, message = weekly.health(staging, 8)
    assert not ok and "no_router" in message


def test_weekly_health_flags_stale_and_missing_runs(tmp_path):
    from datetime import datetime, timedelta, timezone
    weekly = _weekly()
    ok, message = weekly.health(tmp_path, 8)
    assert not ok and "no run recorded" in message
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec="seconds")
    (tmp_path / "last-run.json").write_text(json.dumps({"status": "ok", "ended_at": old}))
    ok, message = weekly.health(tmp_path, 8)
    assert not ok and "10 days old" in message
