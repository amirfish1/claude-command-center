"""One delivery_id for one message, from inject response to queue row to cancel.

Before this, a queued message was known by four unrelated ids (attribution
message_id, worker idempotency_key, receipt inject_id, queue pending_id) and
none reached the browser, so queued cards were matched to the user's echo by
normalised text. These tests pin the contract of ``ccc_server/delivery.py``:
the id the inject response returns is the id the queued event carries, and
cancel by that id removes exactly that row.
"""
import importlib
from pathlib import Path

import pytest

import sender_envelope as attribution

server = importlib.import_module("server")
delivery = importlib.import_module("ccc_server.delivery")
pending_inputs = importlib.import_module("ccc_server.pending_inputs")
stop_holds = importlib.import_module("ccc_server.stop_holds")

SID = "sess-delivery-id"


@pytest.fixture
def queue_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", Path(tmp_path) / "pending-inputs.json")
    server._pending_resume_queue.clear()
    server._pending_terminal_input_queue.clear()
    server._load_pending_inputs()
    monkeypatch.setattr(server, "_log_activity", lambda *a, **k: None)
    monkeypatch.setattr(server, "_schedule_codex_queue_pump", lambda sid: None)
    monkeypatch.setattr(server, "_inject_dedupe_recent", {})
    yield tmp_path
    server._pending_resume_queue.clear()
    server._pending_terminal_input_queue.clear()


@pytest.fixture
def queueing_router(monkeypatch):
    """A router whose engine is busy: every send lands in the terminal queue."""
    def _router(session_id, text, **kwargs):
        out = server._queue_terminal_input(session_id, text)
        assert out.get("ok"), out
        return {"ok": True, "queued": True, "via": "terminal-queued"}

    monkeypatch.setattr(server, "_inject_text_into_session_router", _router)


def _queued_events(sid):
    return [ev for ev in server._get_queued_events_for_session(sid) if ev.get("pending")]


# ── mint ─────────────────────────────────────────────────────────────────────

def test_mint_prefers_attribution_message_id_over_client_key():
    assert delivery.mint(attr={"message_id": "msg-1"}, idempotency_key="inject:abc12345") == "msg-1"


def test_mint_accepts_a_well_formed_client_key():
    assert delivery.mint(idempotency_key="inject:2f1c9d0e-aaaa") == "inject:2f1c9d0e-aaaa"


@pytest.mark.parametrize("bad", ["", "short", "has space in it", "x" * 129, "semi;colon-1"])
def test_mint_falls_back_to_a_server_id_for_unusable_keys(bad):
    minted = delivery.mint(idempotency_key=bad)
    assert minted != bad
    assert len(minted) == 32 and all(c in "0123456789abcdef" for c in minted)


# ── bind / adopt ─────────────────────────────────────────────────────────────

def test_new_row_adopts_bound_id_only_for_the_routed_text():
    def body():
        # Decode paths rebuild existing rows through the constructor: even an
        # identical text there must not take the id meant for the new send.
        decoded = pending_inputs._PendingInputEntry("hello there")
        other = pending_inputs._new_pending_entry("some other message")
        # The enqueue path carries the attribution header; the match ignores it.
        header = attribution.header(attribution.make(who=attribution.author("session", "x", "x")))
        mine = pending_inputs._new_pending_entry(header + "\nhello there\n")
        again = pending_inputs._new_pending_entry("hello there")
        return decoded, other, mine, again

    decoded, other, mine, again = delivery.within("did-1", "hello there", body)
    assert decoded.pending_id != "did-1", "a decode path stole the id"
    assert other.pending_id != "did-1", "a different message took the id"
    assert mine.pending_id == "did-1"
    assert again.pending_id != "did-1", "the binding is single-use"


def test_no_binding_outside_within():
    row = pending_inputs._new_pending_entry("free-standing")
    assert row.pending_id and row.pending_id != ""
    assert delivery.adopt("free-standing") is None


# ── the id round-trips through the inject path ───────────────────────────────

def test_inject_response_id_is_the_queued_event_id(queue_env, queueing_router):
    res = server._inject_text_into_session(SID, "run the review", source="ui",
                                           idempotency_key="inject:11111111-aaaa")
    assert res.get("queued") is True
    assert res.get("delivery_id")
    events = _queued_events(SID)
    assert len(events) == 1
    assert events[0]["delivery_id"] == res["delivery_id"]
    assert delivery.lookup(SID, res["delivery_id"]) == {
        "state": "queued", "queue": "terminal", "text": "run the review",
    }


def test_identical_texts_get_distinct_ids(queue_env, queueing_router):
    a = server._inject_text_into_session(SID, "same words", source="ui",
                                         idempotency_key="inject:aaaaaaaa-0001",
                                         allow_duplicate=True)
    b = server._inject_text_into_session(SID, "same words", source="ui",
                                         idempotency_key="inject:aaaaaaaa-0002",
                                         allow_duplicate=True)
    assert a["delivery_id"] != b["delivery_id"]
    ids = [ev["delivery_id"] for ev in _queued_events(SID)]
    assert sorted(ids) == sorted([a["delivery_id"], b["delivery_id"]])


# ── cancel ───────────────────────────────────────────────────────────────────

def test_cancel_by_id_removes_exactly_that_row(queue_env, queueing_router):
    a = server._inject_text_into_session(SID, "same words", source="ui",
                                         idempotency_key="inject:bbbbbbbb-0001",
                                         allow_duplicate=True)
    b = server._inject_text_into_session(SID, "same words", source="ui",
                                         idempotency_key="inject:bbbbbbbb-0002",
                                         allow_duplicate=True)
    out = delivery.cancel(SID, b["delivery_id"])
    assert out == {"ok": True, "cancelled": 1, "queue": "terminal"}
    remaining = _queued_events(SID)
    assert [ev["delivery_id"] for ev in remaining] == [a["delivery_id"]]
    # Durable: a reload from disk agrees.
    server._pending_terminal_input_queue.clear()
    server._load_pending_inputs()
    assert [ev["delivery_id"] for ev in _queued_events(SID)] == [a["delivery_id"]]


def test_cancel_unknown_id_is_a_no_op_success(queue_env, queueing_router):
    server._inject_text_into_session(SID, "keep me", source="ui",
                                     idempotency_key="inject:cccccccc-0001")
    assert delivery.cancel(SID, "inject:does-not-exist") == {"ok": True, "cancelled": 0}
    assert len(_queued_events(SID)) == 1
    assert delivery.cancel(SID, "")["code"] == "delivery_id_required"
    assert delivery.lookup(SID, "inject:does-not-exist") is None


# ── stop-held rows share the id space ────────────────────────────────────────

def test_held_row_keeps_its_id_and_cancels_by_it(queue_env, queueing_router):
    res = server._inject_text_into_session(SID, "stale instruction", source="ui",
                                           idempotency_key="inject:dddddddd-0001")
    assert server._hold_queued_inputs_for_stop(SID, "stop-steer") == 1
    assert _queued_events(SID) == []
    held = delivery.lookup(SID, res["delivery_id"])
    assert held == {"state": "held", "queue": "stop_held", "text": "stale instruction"}
    view_ids = [i["id"] for i in server._stop_held_inputs_view(SID)["items"]]
    assert view_ids == [res["delivery_id"]]
    assert delivery.cancel(SID, res["delivery_id"]) == {"ok": True, "cancelled": 1, "queue": "stop_held"}
    assert server._stop_held_inputs_view(SID)["items"] == []


# ── the id survives the dashboard -> worker hop ──────────────────────────────

def test_control_plane_call_carries_the_bound_id(monkeypatch):
    seen = []
    monkeypatch.setattr(server, "_control_plane_routes_engines", lambda: True)

    def fake_request(method, params=None, **kwargs):
        seen.append((method, params))
        return {"available": True, "ok": True}

    monkeypatch.setattr(server, "_control_plane_request", fake_request)
    delivery.within("did-hop", "hello", lambda: server._control_plane_engine_call(
        "codex", "resume", {"session_id": "s", "text": "hello"}, idempotency_key="k"))
    delivery.within("did-hop", "hello", lambda: server._control_plane_engine_call(
        "codex", "activity", {"session_id": "s"}, mutate=False))
    server._control_plane_engine_call("codex", "resume", {"session_id": "s", "text": "x"},
                                      idempotency_key="k2")
    assert seen[0][1]["args"]["delivery_id"] == "did-hop"
    assert "delivery_id" not in seen[1][1]["args"], "reads stay byte-identical"
    assert "delivery_id" not in seen[2][1]["args"], "no binding, no id"


def test_worker_rebinds_the_id_around_the_engine_call(queue_env, monkeypatch):
    from unittest import mock
    from worker_engines import EngineHost

    class Runtime:
        ledger = mock.Mock()

    host = EngineHost(Runtime())
    host._module = server
    monkeypatch.setattr(
        server, "resume_session_codex",
        lambda sid, text, **kw: server._queue_codex_resume(sid, text, reason="busy"),
    )
    out = host._call("codex", "resume", {"session_id": SID, "text": "hello", "delivery_id": "did-w"})
    assert out.get("queued") is True
    assert [ev["delivery_id"] for ev in _queued_events(SID)] == ["did-w"]
    assert delivery.bound_id() is None, "the binding does not leak past the call"
