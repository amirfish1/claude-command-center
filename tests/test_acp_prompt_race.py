"""Regression coverage for ACP prompt lifecycle ordering."""

import importlib
from unittest import mock


def test_immediate_acp_prompt_completion_does_not_leave_a_phantom_active_turn():
    """A response may arrive before ``_acp_request_async`` returns.

    The prompt must be registered as active before its response can be folded;
    otherwise completion is observed first and the later prompt bookkeeping
    resurrects a permanent active/Thinking state.
    """
    server = importlib.import_module("server")
    harness = "acp-prompt-race"
    sid = "session-immediate-response"
    server._ACP_PENDING.pop(harness, None)
    server._ACP_SESSION_STATE.pop(harness, None)
    try:
        def respond_before_return(_harness, method, _params, sid=None, on_registered=None,
                                  on_send_failed=None):
            assert _harness == harness
            assert method == "session/prompt"
            req_id = 1
            with server._ACP_LOCK:
                entry = {
                    "event": server.threading.Event(),
                    "response": None,
                    "method": method,
                    "sid": sid,
                    "is_active": False,
                }
                server._ACP_PENDING.setdefault(harness, {})[req_id] = entry
                assert on_registered is not None
                on_registered(req_id, entry)
            server._acp_handle_message(harness, {
                "jsonrpc": "2.0", "id": req_id,
                "result": {"stopReason": "end_turn"},
            })
            return req_id

        with mock.patch.object(server, "_acp_ensure_session_loaded", return_value=None), \
             mock.patch.object(server, "_acp_request_async", side_effect=respond_before_return):
            result = server._acp_prompt(harness, sid, "hello")

        assert result["ok"] is True
        with server._ACP_LOCK:
            state = server._acp_session(harness, sid)
            assert state["status"] == "idle"
            assert state["active_turn"] is None
    finally:
        server._ACP_PENDING.pop(harness, None)
        server._ACP_SESSION_STATE.pop(harness, None)
