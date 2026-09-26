"""One-click Claude Code re-authentication (ccc_server/claude_auth.py).

The login flow runs for real: a real tmux session, driving a stand-in
`claude` script (CCC_CLAUDE_BIN) that behaves like `claude auth login`:
prints the authorize URL, reads the pasted code, writes its logged-in state.
"""

import json
import uuid

import pytest

import server
from ccc_server import claude_auth as ca
from ccc_server import fleet


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _assistant(text, **extra):
    ev = {
        "type": "assistant",
        "timestamp": "2026-09-26T10:00:00Z",
        "message": {"id": "m-" + uuid.uuid4().hex[:6], "model": "<synthetic>",
                    "content": [{"type": "text", "text": text}]},
    }
    ev.update(extra)
    return ev


# -- detection ---------------------------------------------------------------


def test_synthetic_auth_error_turn_flags_the_row(tmp_path):
    t = tmp_path / "s.jsonl"
    _write_jsonl(t, [_assistant("Not logged in · Please run /login",
                                error="authentication_failed", isApiErrorMessage=True)])
    meta = server._extract_tail_meta(t)
    assert meta["last_api_error"] == "authentication_failed"
    assert ca.claude_auth_failed_from_meta(meta) is True


def test_later_real_turn_clears_the_flag(tmp_path):
    t = tmp_path / "s.jsonl"
    _write_jsonl(t, [
        _assistant("Failed to authenticate. OAuth session expired and could not be refreshed",
                   error="authentication_failed", isApiErrorMessage=True),
        _assistant("All good now, continuing."),
    ])
    meta = server._extract_tail_meta(t)
    assert meta["last_api_error"] is None
    assert ca.claude_auth_failed_from_meta(meta) is False


def test_text_fallback_only_for_short_messages():
    short = {"last_assistant_text": "Failed to authenticate: OAuth session expired and could not be refreshed"}
    assert ca.claude_auth_failed_from_meta(short) is True
    long_answer = {"last_assistant_text": "Here is how to handle 'Failed to authenticate' errors. " * 30}
    assert ca.claude_auth_failed_from_meta(long_answer) is False
    assert ca.claude_auth_failed_from_meta({"last_assistant_text": "claude is not authenticated"}) is True
    assert ca.claude_auth_failed_from_meta({"last_api_error": "rate_limit",
                                            "last_assistant_text": "Not logged in · Please run /login"}) is False


# -- gating + routing --------------------------------------------------------


def test_endpoints_are_preview_gated(monkeypatch):
    monkeypatch.delenv("CCC_FF_CLAUDE_REAUTH", raising=False)
    payload, status = ca.claude_auth_handle("start", {})
    assert status == 403 and payload["error"] == "feature_disabled"
    assert ca.claude_auth_handle("bogus", {"via_route": True})[1] == 404


def test_route_table_dedupes_submit_and_stamps_via_route(monkeypatch):
    spec = fleet._FEDERATION_ROUTE_ACTIONS
    assert spec["claude_auth_submit"] == ("POST", "/api/claude-auth/submit", True)
    assert spec["claude_auth_start"][2] is False
    seen = {}

    def fake_self_api(method, api_path, body=None, query=None, timeout=60.0):
        seen.update(method=method, path=api_path, body=body, timeout=timeout)
        return {"ok": True}

    monkeypatch.setattr(fleet, "_federation_self_api", fake_self_api)
    payload, status = fleet._federation_execute_route(
        {"action": "claude_auth_start", "args": {}, "hops": 2, "req_id": ""})
    assert status == 200 and payload["result"] == {"ok": True}
    assert seen["path"] == "/api/claude-auth/start"
    assert seen["body"]["via_route"] is True


def test_nudge_dedupes_and_skips_bad_ids():
    calls = []

    def inject(sid, text):
        calls.append((sid, text))
        return {"ok": True}

    out = ca.claude_auth_nudge(["abcd-1234", "abcd-1234", "bad id; rm", "", "efgh-5678"], inject)
    assert [c[0] for c in calls] == ["abcd-1234", "efgh-5678"]
    assert out["nudged"] == 2
    assert "wt claim" in calls[0][1]


# -- the real tmux flow ------------------------------------------------------

GOOD_CODE = "goodcode1234567890#stateabc"


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    if not ca._claude_auth_tmux_bin():
        pytest.skip("tmux not installed")
    state = tmp_path / "logged-in"
    script = tmp_path / "claude"
    # The tmux server may predate this test, so state paths are baked into
    # the script rather than passed through the environment.
    script.write_text(f"""#!/bin/sh
if [ "$1 $2" = "auth login" ]; then
  echo "Opening browser to sign in..."
  echo "If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&state=stateabc"
  printf "Paste code here if prompted > "
  read code
  if [ "$code" = "{GOOD_CODE}" ]; then touch "{state}"; echo "Login successful."; else echo "OAuth error: Invalid code"; fi
elif [ "$1 $2" = "auth status" ]; then
  if [ -f "{state}" ]; then echo '{{"loggedIn": true, "email": "t@example.com", "authMethod": "claude.ai"}}'
  else echo '{{"loggedIn": false}}'; fi
elif [ "$1" = "-p" ]; then
  echo ok
fi
""")
    script.chmod(0o755)
    monkeypatch.setenv("CCC_CLAUDE_BIN", str(script))
    monkeypatch.delenv("CCC_CLAUDE_AUTH_USER", raising=False)
    monkeypatch.setattr(ca, "CLAUDE_AUTH_TMUX_SESSION", "ccc-claude-auth-test-" + uuid.uuid4().hex[:6])
    monkeypatch.setattr(ca, "_CLAUDE_AUTH_SUBMIT_WAIT_S", 10.0)
    ca._claude_auth_state.update(state="idle", attempt_id=None, url=None)
    yield state
    ca.claude_auth_cancel()


def test_full_login_flow(fake_claude):
    assert ca.claude_auth_status()["logged_in"] is False

    first = ca.claude_auth_start()
    assert first["ok"], first
    assert first["url"].startswith("https://claude.com/cai/oauth/authorize?")
    # Re-clicking Start must not mint new PKCE state under the user.
    again = ca.claude_auth_start()
    assert again["reused"] and again["attempt_id"] == first["attempt_id"]

    bad = ca.claude_auth_submit(first["attempt_id"], "not a code!")
    assert bad["error"] == "bad_code"
    assert ca.claude_auth_public_state()["state"] == "awaiting_code"
    assert ca.claude_auth_submit("wrong-attempt", GOOD_CODE)["error"] == "stale_attempt"

    done = ca.claude_auth_submit(first["attempt_id"], GOOD_CODE)
    assert done["ok"] and done["logged_in"], done
    assert done["email"] == "t@example.com"
    assert done["smoke"]["ok"] is True
    assert GOOD_CODE not in json.dumps(done)
    assert GOOD_CODE not in json.dumps(ca.claude_auth_public_state())
    assert not ca._claude_auth_session_alive()


def test_wrong_code_fails_without_echoing_it(fake_claude):
    first = ca.claude_auth_start()
    wrong = "wrongcode9999999999#stateabc"
    out = ca.claude_auth_submit(first["attempt_id"], wrong)
    assert out["ok"] is False and out["error"] == "login_failed"
    assert wrong not in json.dumps(out)
    assert "wrongcode9999999999" not in json.dumps(ca.claude_auth_public_state())
    # The attempt is spent: its code cannot be retried against it.
    assert ca.claude_auth_submit(first["attempt_id"], GOOD_CODE)["error"] == "stale_attempt"


def test_missing_tmux_is_a_typed_error(monkeypatch):
    monkeypatch.setattr(ca, "_claude_auth_tmux_bin", lambda: None)
    ca._claude_auth_state.update(state="idle", attempt_id=None, url=None)
    out = ca.claude_auth_start()
    assert out == {"ok": False, "error": "tmux_missing", "detail": out["detail"]}

