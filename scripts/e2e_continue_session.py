"""Hermetic E2E: limit hit -> same session continues on a router -> reset -> back.

Real Claude Code CLI, real CCC failover code, two fake Anthropic-compatible
endpoints on loopback (no network, no spend):

  * "plan"   — stands in for the user's Claude plan. Serves the seed turn and
               the post-reset turn.
  * "router" — stands in for the user's router. Must serve every turn in
               between, including a turn whose warm process was retired
               (a fresh `claude --resume` spawn), and must only ever see the
               router's own token and a non-Claude model (D9).

The limit is simulated by appending a "Claude AI usage limit reached" result
to the real transcript; the reset is simulated by running the watcher pass
with a clock past the recorded reset. Everything lives in a throwaway HOME.

Prints one ``E2E_RESULT {json}`` line on success; exits non-zero otherwise.
Usage: python3 scripts/e2e_continue_session.py [EMPTY_HOME_DIR]
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


REPO = Path(__file__).resolve().parent.parent
PLAN_TOKEN = "plan-test-XXXX"
ROUTER_TOKEN = "router-test-XXXX"
ROUTER_MODEL = "e2e-router/free-model"
TIMEOUT_S = int(os.environ.get("CCC_E2E_SPAWN_TIMEOUT_S", "180"))


class E2EError(RuntimeError):
    pass


def require(cond, msg):
    if not cond:
        raise E2EError(msg)


class FakeAnthropic:
    """Minimal Anthropic Messages API: every turn answers with `reply`."""

    def __init__(self, name):
        self.name = name
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._json(200, {})

            def do_HEAD(self):
                self._json(200, {})

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                try:
                    body = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    body = {}
                path = self.path.split("?")[0]
                if path.endswith("/count_tokens"):
                    return self._json(200, {"input_tokens": 1})
                if not path.endswith("/v1/messages"):
                    return self._json(404, {"type": "error", "error": {"type": "not_found_error", "message": path}})
                auth = self.headers.get("authorization") or ""
                fake.requests.append({
                    "model": body.get("model"),
                    "auth": auth.split(" ", 1)[-1] if auth else "",
                    "api_key": self.headers.get("x-api-key") or "",
                })
                text = f"{fake.name.upper()}-OK"
                msg_id = "msg_" + uuid.uuid4().hex[:20]
                model = body.get("model") or "unknown"
                if not body.get("stream"):
                    return self._json(200, {
                        "id": msg_id, "type": "message", "role": "assistant", "model": model,
                        "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn", "stop_sequence": None,
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    })
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("cache-control", "no-cache")
                self.end_headers()
                events = [
                    ("message_start", {"type": "message_start", "message": {
                        "id": msg_id, "type": "message", "role": "assistant", "model": model,
                        "content": [], "stop_reason": None, "stop_sequence": None,
                        "usage": {"input_tokens": 1, "output_tokens": 0}}}),
                    ("content_block_start", {"type": "content_block_start", "index": 0,
                                             "content_block": {"type": "text", "text": ""}}),
                    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                             "delta": {"type": "text_delta", "text": text}}),
                    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                    ("message_delta", {"type": "message_delta",
                                       "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                       "usage": {"output_tokens": 1}}),
                    ("message_stop", {"type": "message_stop"}),
                ]
                for name, data in events:
                    self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()


def rows(path):
    out = []
    try:
        for line in Path(path).read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    except OSError:
        pass
    return out


def wait(predicate, what, seconds=TIMEOUT_S):
    deadline = time.time() + seconds
    while time.time() < deadline:
        got = predicate()
        if got:
            return got
        time.sleep(0.5)
    raise E2EError(f"timed out waiting for {what}")


def main():
    real_home = Path.home()
    home = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ccc-e2e-continue-")).absolute()
    require(home.resolve() != real_home.resolve(), "refusing to run in the real HOME")
    home.mkdir(parents=True, exist_ok=True)
    require(not any(home.iterdir()), "the test HOME must be empty")
    claude = shutil.which("claude")
    require(claude, "claude CLI not on PATH")

    plan, router = FakeAnthropic("plan"), FakeAnthropic("router")
    playground = home / "playground"
    playground.mkdir()
    subprocess.run(["git", "init", "-q", str(playground)], check=True)

    base_env = {k: v for k, v in os.environ.items()
                if k in ("PATH", "LANG", "LC_ALL", "TMPDIR", "PYTHONPATH", "PYTHONUSERBASE")}
    base_env.update({
        "HOME": str(home),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        # The "plan": what every non-failover turn uses.
        "ANTHROPIC_BASE_URL": plan.url,
        "ANTHROPIC_AUTH_TOKEN": PLAN_TOKEN,
    })

    # 1. Seed a real conversation on the plan.
    sid = str(uuid.uuid4())
    seed = subprocess.run(
        [claude, "-p", "--verbose", "--session-id", sid, "--output-format", "stream-json",
         "--setting-sources", "project", "--tools", "", "--", "Say hi."],
        cwd=playground, env=base_env, capture_output=True, text=True, timeout=TIMEOUT_S)
    results = [json.loads(l) for l in seed.stdout.splitlines() if l.startswith("{")]
    results = [r for r in results if r.get("type") == "result"]
    require(results and results[-1].get("subtype") == "success",
            f"seed turn failed: {seed.stdout[-800:]} {seed.stderr[-800:]}")
    transcripts = list((home / ".claude/projects").glob(f"*/{sid}.jsonl"))
    require(len(transcripts) == 1, "no real transcript for the seed session")
    transcript = transcripts[0]
    seed_history = {r.get("uuid") for r in rows(transcript) if r.get("type") in ("user", "assistant")}
    require(len(plan.requests) >= 1 and not router.requests, "seed did not run on the plan")

    # 2. Simulated limit stop on that real transcript.
    with open(transcript, "a") as fh:
        fh.write(json.dumps({
            "type": "result", "subtype": "error_during_execution", "is_error": True,
            "result": "Claude AI usage limit reached", "sessionId": sid, "session_id": sid,
            "uuid": str(uuid.uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(),
        }) + "\n")

    # 3. Real CCC code in this process, pointed at the throwaway HOME.
    os.environ.clear()
    os.environ.update(base_env)
    os.environ.update({
        "CCC_EPHEMERAL": "1", "CCC_CONTROL_PLANE_ENGINES": "0", "CCC_TELEMETRY_DISABLED": "1",
        "CCC_CLAUDE_BIN": claude, "CCC_FREE_ROUTER_HOME": str(home / ".ccc"),
        # The user's router (self-hosted env seam of _free_spawn_env).
        "CCC_FREE_ROUTER_BASE_URL": router.url,
        "CCC_FREE_ROUTER_TOKEN": ROUTER_TOKEN,
        "CCC_FREE_ROUTER_MODEL": ROUTER_MODEL,
    })
    os.chdir(playground)
    sys.path.insert(0, str(REPO))
    import server  # noqa: E402

    server._usage_limit_scan_once()
    status = server.free_failover_status()["sessions"].get(sid) or {}
    require(status.get("state") == "limited" and status.get("supports_continue_free"),
            f"limit watcher did not offer Continue: {status}")
    require(len(router.requests) == 0, "router used before approval")

    def turn_done(n_results):
        def check():
            logs = list(home.rglob(f"resume-{sid[:8]}-*.log"))
            done = [r for log in logs for r in rows(log) if r.get("type") == "result"]
            return len(done) >= n_results and done
        return check

    def settle(n_results):
        done = wait(turn_done(n_results), f"resumed turn {n_results}")
        require(not done[-1].get("is_error"), f"resumed turn {n_results} errored: {done[-1]}")

    # 4. Hop 1: approved Continue -> same session on the router.
    plan_before = len(plan.requests)
    res = server.free_failover_continue(sid)
    require(res.get("ok") and res.get("session_id") == sid, f"continue failed: {res}")
    settle(1)
    require(len(router.requests) >= 1, "hop 1 did not reach the router")
    require(len(plan.requests) == plan_before, "hop 1 leaked a turn to the plan")

    # 5. Same session, later turn, warm process gone: still the router.
    wait(lambda: server._retire_idle_headless_for_session(sid, reason="e2e").get("retired")
         or server._find_live_spawn_entry_for_session(sid) is None, "free warm process to retire", 60)
    router_before = len(router.requests)
    res = server.resume_session_headless(sid, "One more, please.")
    require(res.get("ok"), f"follow-up resume failed: {res}")
    settle(2)
    require(len(router.requests) > router_before, "follow-up turn left the router")
    require(len(plan.requests) == plan_before, "follow-up turn hit the still-limited plan")
    require(server.free_failover_status()["sessions"][sid]["state"] == "free", "not recorded as free")

    # 6. Claude resets -> watcher pass switches back (no prompt sent).
    rec = server._load_free_failovers()[sid]
    reset_at = float(rec.get("origin_resume_at") or time.time())
    router_before, plan_before = len(router.requests), len(plan.requests)

    def switched():
        server._free_failover_auto_pass(now=reset_at + 1)
        st = (server._load_free_failovers().get(sid) or {}).get("state")
        return st not in ("free", "switch_back_pending")
    wait(switched, "switch back at reset", 60)
    require(len(router.requests) == router_before and len(plan.requests) == plan_before,
            "switch back sent a model request")

    # 7. Next turn is back on the plan.
    wait(lambda: server._find_live_spawn_entry_for_session(sid) is None, "free child to exit", 30)
    res = server.resume_session_headless(sid, "Back on the plan?")
    require(res.get("ok"), f"post-reset resume failed: {res}")
    settle(3)
    require(len(plan.requests) > plan_before, "post-reset turn did not reach the plan")
    require(len(router.requests) == router_before, "post-reset turn still went to the router")
    live = server._find_live_spawn_entry_for_session(sid)
    if live is not None:
        server._retire_idle_headless_for_session(sid, reason="e2e-cleanup")

    # 8. Evidence: one marker per hop, same session, history kept, D9 held.
    all_rows = rows(transcript)
    markers = [r for r in all_rows if r.get("subtype") == "ccc_free_runtime"]
    require([m["event"] for m in markers] == ["failover_start", "failover_back"],
            f"unexpected markers: {[m.get('event') for m in markers]}")
    require(all(m.get("sessionId") == sid for m in markers), "marker on another session")
    require(seed_history <= {r.get("uuid") for r in all_rows}, "seed history lost")
    require(all(r["auth"] == ROUTER_TOKEN or r["api_key"] == ROUTER_TOKEN for r in router.requests),
            "router saw a credential that is not the router token")
    require(all(PLAN_TOKEN not in (r["auth"], r["api_key"]) for r in router.requests),
            "router saw the plan credential")
    require(not any(server._is_claude_model(r["model"]) for r in router.requests),
            f"router carried a Claude model: {[r['model'] for r in router.requests]}")
    texts = " ".join(json.dumps(r.get("message", {}).get("content", "")) for r in all_rows if r.get("type") == "assistant")
    require("ROUTER-OK" in texts and "PLAN-OK" in texts, "transcript lacks replies from both hops")

    result = {
        "ok": True, "session_id": sid, "transcript_file": str(transcript),
        "markers": [{"event": m["event"], "text": m["text"]} for m in markers],
        "router_requests": len(router.requests), "plan_requests": len(plan.requests),
        "router_models": sorted({r["model"] for r in router.requests if r["model"]}),
    }
    print("E2E_RESULT " + json.dumps(result), flush=True)
    plan.stop()
    router.stop()


if __name__ == "__main__":
    try:
        main()
    except E2EError as exc:
        print(f"E2E_FAIL {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
