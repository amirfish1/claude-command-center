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


# ── receipts settle by the same id ───────────────────────────────────────────

receipts = importlib.import_module("ccc_server.inject_receipts")


def _queue_with_receipt(text):
    out = server._inject_text_into_session(SID, text)
    did = out["delivery_id"]
    receipts.open_receipt(SID, did, text, source="composer")
    return did


def test_claim_commit_settles_the_receipt_by_id(queue_env, queueing_router):
    did = _queue_with_receipt("deliver me")
    assert delivery.outstanding(SID, stale_after_s=0)["state"] == "queued"
    claimed = server._apply_pending_input_operations(SID, [{
        "field": "terminal", "action": "pop_head_claim",
    }])["value"][0]
    assert delivery.entry_id(claimed["item"]) == did
    assert server._commit_pending_input_claim({
        "session_id": SID, "queue_name": "terminal", "index": 0,
        "item": claimed["item"], "claim_sequence": claimed["claim_sequence"],
    }) is True
    assert delivery.outstanding(SID, stale_after_s=0) is None
    state = receipts._load(receipts._default_path())
    assert [e["inject_id"] for e in state["_landed"]] == [did]


def test_commit_of_an_older_row_keeps_the_newer_receipt_open(queue_env, queueing_router):
    first = _queue_with_receipt("first")
    second = _queue_with_receipt("second")
    claimed = server._apply_pending_input_operations(SID, [{
        "field": "terminal", "action": "pop_head_claim",
    }])["value"][0]
    assert delivery.entry_id(claimed["item"]) == first
    server._commit_pending_input_claim({"session_id": SID, "item": claimed["item"]})
    still = delivery.outstanding(SID, stale_after_s=0)
    assert still["inject_id"] == second and still["state"] == "queued"


def test_cancel_by_id_settles_the_receipt_as_dropped(queue_env, queueing_router):
    did = _queue_with_receipt("never mind")
    assert delivery.cancel(SID, did)["cancelled"] == 1
    assert delivery.outstanding(SID, stale_after_s=0) is None
    state = receipts._load(receipts._default_path())
    assert [e["inject_id"] for e in state["_dropped"]] == [did]


def test_outstanding_names_where_the_message_waits(queue_env, queueing_router):
    did = _queue_with_receipt("where am i")
    view = delivery.outstanding(SID, stale_after_s=0)
    assert (view["state"], view["queue"]) == ("queued", "terminal")
    server._apply_pending_input_operations(SID, [{
        "field": "terminal", "action": "pop_head_claim",
    }])
    view = delivery.outstanding(SID, stale_after_s=0)
    assert (view["state"], view["queue"]) == ("unproven", None)
    assert view["inject_id"] == did


def test_unproven_receipt_expires_after_a_day(queue_env, queueing_router):
    did = _queue_with_receipt("lost")
    server._apply_pending_input_operations(SID, [{
        "field": "terminal", "action": "pop_head_claim",
    }])
    later = __import__("time").time() + receipts.EXPIRE_AFTER_S + 1
    assert delivery.outstanding(SID, now=later, stale_after_s=0) is None
    state = receipts._load(receipts._default_path())
    assert [e["inject_id"] for e in state["_expired"]] == [did]
    assert delivery.outstanding(SID, now=later, stale_after_s=0) is None


# ── slice 3: steer and release name the row by id ────────────────────────────

def _two_rows_same_text(text="same words"):
    a = server._inject_text_into_session(SID, text, source="ui",
                                         idempotency_key="inject:eeeeeeee-0001",
                                         allow_duplicate=True)["delivery_id"]
    b = server._inject_text_into_session(SID, text, source="ui",
                                         idempotency_key="inject:eeeeeeee-0002",
                                         allow_duplicate=True)["delivery_id"]
    assert [ev["delivery_id"] for ev in _queued_events(SID)] == [a, b]
    return a, b


def test_claim_by_id_takes_that_row_not_the_first_text_match(queue_env, queueing_router):
    a, b = _two_rows_same_text()
    claim = server._claim_matching_pending_input(SID, "same words", pending_id=b)
    assert delivery.entry_id(claim["item"]) == b and claim["index"] == 1
    assert [ev["delivery_id"] for ev in _queued_events(SID)] == [a]
    # A row that already left the queue is not silently swapped for a twin.
    assert server._claim_matching_pending_input(SID, "same words", pending_id=b) is None
    assert server._restore_pending_input_claim(claim)
    assert [ev["delivery_id"] for ev in _queued_events(SID)] == [a, b]


def test_consume_by_id_wins_over_text_and_falls_back_to_text(queue_env, queueing_router):
    a, b = _two_rows_same_text()
    assert server._consume_matching_pending_input(SID, "same words", pending_id=b) == 1
    assert [ev["delivery_id"] for ev in _queued_events(SID)] == [a]
    # Finalisers run after delivery: an unknown id must still withdraw the
    # queued copy by text, or the pump would deliver it a second time.
    assert server._consume_matching_pending_input(SID, "same words", pending_id="gone") == 1
    assert _queued_events(SID) == []


def test_released_hold_keeps_the_delivery_id_and_receipt(queue_env, queueing_router):
    did = _queue_with_receipt("resume this later")
    assert server._hold_queued_inputs_for_stop(SID, "stop-steer") == 1
    assert delivery.outstanding(SID, stale_after_s=0)["state"] == "held"
    out = server._release_stop_held_inputs(SID, [did])
    assert out["released"] == 1 and out["held"]["items"] == []
    assert [ev["delivery_id"] for ev in _queued_events(SID)] == [did]
    view = delivery.outstanding(SID, stale_after_s=0)
    assert (view["inject_id"], view["state"], view["queue"]) == (did, "queued", "terminal")
    # Same message, same id: cancel by the id the browser already holds.
    assert delivery.cancel(SID, did)["cancelled"] == 1


def test_explicit_delivery_id_overrides_minting(queue_env, queueing_router):
    out = server._inject_text_into_session(SID, "pinned", source="ui",
                                           delivery_id="inject:pinned-0001")
    assert out["delivery_id"] == "inject:pinned-0001"
    assert [ev["delivery_id"] for ev in _queued_events(SID)] == ["inject:pinned-0001"]


def test_codex_steer_by_id_claims_that_row(queue_env, monkeypatch):
    """The browser's replace_queued_ids reaches the Codex claim transaction."""
    from unittest import mock
    for name in ("_is_cursor_session", "_is_hermes_session", "_is_kimi_session",
                 "_is_opencode_session", "_is_devin_cli_session"):
        monkeypatch.setattr(server, name, lambda sid: False)
    monkeypatch.setenv("CCC_CODEX_DESKTOP_IPC", "0")
    monkeypatch.setattr(server, "_resolve_codex_bin",
                        lambda: {"available": True, "bin": "/usr/bin/codex-test"})
    monkeypatch.setattr(server, "_inject_budget_check", lambda *args: None)
    monkeypatch.setattr(server, "_is_codex_session", lambda sid: True)
    monkeypatch.setattr(server, "find_session_cwd", lambda sid: "/tmp")
    monkeypatch.setattr(server, "session_live_status",
                        lambda sid, cwd=None: {"live": True, "status": "working", "tty": None})
    monkeypatch.setattr(server, "_session_acp_harness", lambda sid: None)
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *a, **k: None)
    monkeypatch.setattr(server, "_save_codex_app_server_state_unlocked", mock.Mock())
    seen = []

    def deliver(sid, text, **kwargs):
        seen.append([ev["delivery_id"] for ev in _queued_events(SID)])
        return {"ok": True, "via": "codex-steer"}

    monkeypatch.setattr(server, "_resume_session_codex_native_delivery", deliver)
    # The resume queue collapses identical text; the terminal queue keeps
    # both twins, and the claim searches it too.
    ids = []
    for key in ("inject:ffffffff-0001", "inject:ffffffff-0002"):
        assert delivery.within(key, "twin", lambda: server._queue_terminal_input(SID, "twin"))["ok"]
        ids.append(key)
    assert [ev["delivery_id"] for ev in _queued_events(SID)] == ids

    result = server._inject_text_into_session_router(
        SID, "twin", mode="steer", preserve_queued_steer=True,
        replace_queued_ids=[ids[1]],
    )
    assert result["ok"] and result["queued_consumed"] == 1
    assert seen == [[ids[0]]]
    assert [ev["delivery_id"] for ev in _queued_events(SID)] == [ids[0]]


def test_control_plane_resume_carries_replace_queued_id(monkeypatch):
    calls = []

    def fake_call(engine, operation, args, **kwargs):
        calls.append((engine, operation, dict(args)))
        return {"ok": True, "via": "codex-steer"}

    monkeypatch.setattr(server, "_control_plane_engine_call", fake_call)
    monkeypatch.setattr(server, "_pending_writer_compatibility_status", lambda: {"ok": True})
    server.resume_session_codex(SID, "twin", steer=True, preserve_queued_steer=True,
                                replace_queued_id="row-b")
    server.resume_session_codex(SID, "twin", steer=True)
    assert calls[0][2]["replace_queued_id"] == "row-b"
    assert "replace_queued_id" not in calls[1][2]


def test_worker_forwards_replace_queued_id_only_when_named():
    from types import SimpleNamespace
    from unittest import mock
    from worker_engines import EngineHost
    resume = mock.Mock(return_value={"ok": True})
    host = object.__new__(EngineHost)
    host._legacy = lambda: SimpleNamespace(resume_session_codex=resume)
    host._call("codex", "resume", {"session_id": SID, "text": "twin", "steer": True,
                                   "replace_queued_id": "row-b"})
    assert resume.call_args.kwargs["replace_queued_id"] == "row-b"
    host._call("codex", "resume", {"session_id": SID, "text": "twin", "steer": True})
    assert "replace_queued_id" not in resume.call_args.kwargs


def test_inject_route_reads_replace_queued_ids():
    server_py = Path(server.__file__).read_text()
    assert 'payload.get("replace_queued_ids")' in server_py
    assert 'inject_options["replace_queued_ids"] = replace_queued_ids' in server_py
    assert "ids=replace_queued_ids" in server_py
