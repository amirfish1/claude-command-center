"""Steering a message must not turn it into the user's typing.

A Steer click re-sends words that already exist. The dashboard form looks the same
whoever wrote them, so the server keeps the original sender, and Share leaves out
anything it can't prove. The other tests cover row shapes that review found.
"""
import http.server
import json
import threading
import time
import urllib.request
from pathlib import Path

import pytest

import sender_envelope as attribution
import server
from ccc_server import codex_parse, share_run, ship_graph
from ccc_server.share_redact import RedactionError

TS = "2026-01-01T00:00:%02dZ"
EPOCH = 1767225600.0
SID = "sess-steer-origin"
PEER = "Please rotate the invoice ledger before Tuesday"
OTHER = "Also close the second ledger today"
WORD = "ledger"
COMPOSER_FORM = {"source": "composer", "composer_form": "ccc-composer-v1"}


@pytest.fixture(autouse=True)
def no_real_hide_list(monkeypatch):
    monkeypatch.setattr(share_run, "_user_hide_list", lambda: [])
    monkeypatch.setenv("CCC_ATTRIBUTION_EMIT", "1")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("WATCHTOWER_STATE_DIR", str(tmp_path / "wt"))
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", Path(tmp_path) / "pending-inputs.json")
    server._pending_resume_queue.clear()
    server._pending_terminal_input_queue.clear()
    server._load_pending_inputs()
    monkeypatch.setattr(server, "_log_activity", lambda *a, **k: None)
    monkeypatch.setattr(server, "_record_interaction", lambda sid: None)
    # A composer send reopens the session, which warms the ship graph on a thread.
    monkeypatch.setattr(ship_graph, "_start_background_sync", lambda *a, **k: None)
    delivered = []

    def router(sid, text, **kw):                    # only the last hop is replaced
        delivered.append(text)
        return {"ok": True, "via": "mock"}

    monkeypatch.setattr(server, "_inject_text_into_session_router", router)
    yield delivered
    server._pending_resume_queue.clear()
    server._pending_terminal_input_queue.clear()


def peer_attr(name="agent-b", ident="01a19455-2e62-72d1-bfc5-929aae0ccece"):
    return attribution.make(SID, who=attribution.author("session", ident, name, "codex"), basis="claimed")


def queue_peer(body, attr=None):
    assert server._queue_terminal_input(SID, attribution.carry(body, attr or peer_attr(), app="ccc")).get("ok")


def queue_typed(body):
    attr = attribution.dashboard_human(SID, body)
    attribution.journal(attr, app="ccc", event="inject")
    attribution.during(attr, lambda: server._queue_terminal_input(SID, body))


def cards():
    return [e for e in server._get_queued_events_for_session(SID) if e.get("pending")]


def click(**payload):
    """POST what static/app.js posts, through the real handler."""
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    body = {"session_id": SID, "idempotency_key": "inject-steer-origin", "mode": "steer", **COMPOSER_FORM, **payload}
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/inject-input", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    finally:
        httpd.shutdown()


def steer_card():
    (card,) = cards()
    return click(text=card["text"], replace_queued=True, replace_queued_ids=[card.get("delivery_id") or ""])


def steer_all():
    rows = cards()
    texts = [c["text"] for c in rows]
    return click(text="\n\n".join(texts), replace_queued=True, replace_queued_texts=texts,
                 replace_queued_ids=[c.get("delivery_id") or "" for c in rows])


def sends():
    return attribution.human_sends(SID, [time.strftime("%Y-%m", time.gmtime())])


def steps(events, engine="claude", **kw):
    """User steps on the page, or None when refused (safe)."""
    try:
        replay, _ = share_run.build_replay(events, engine=engine, identity={}, **kw)
    except RedactionError:
        return None
    return [s["text"] for s in replay["steps"] if s["kind"] == "user"]


def reply(i=9):
    return {"type": "assistant", "ts": TS % i, "message_id": "m1", "line": 900 + i,
            "blocks": [{"kind": "text", "text": "Done with that."}]}


def row(text, line, i=0, **extra):
    return {"type": "user_text", "text": text, "ts": TS % i, "line": line, **extra}


def typed_row(text, line=1, i=0):
    return row(text, line, i, entrypoint="cli", turn_origin="human", origin_kind="human")


def exported(delivered, **kw):
    return steps([typed_row("Start the work"), row(delivered, 2, 3, turn_origin="sdk"), reply()], **kw)


def assert_stays_out(got):
    assert got is None or not any(WORD in t for t in got), f"another agent's words shown as you: {got}"


def parse(rows):
    events = (server._parse_conversation_event(r, n) for n, r in enumerate(rows, 1))
    return [e for e in events if e]


def claude_row(text, i=0, **extra):
    r = {"type": "user", "timestamp": TS % i, "uuid": f"u{i}", "message": {"role": "user", "content": text}}
    r.update(extra)
    return r


CLI_START = claude_row("Start the work", 0, entrypoint="cli", turnOrigin="human", origin={"kind": "human"})


# ---- Steer on a queued card ---------------------------------------------------

def test_steer_keeps_the_senders_header_and_records_no_typing(env):
    queue_peer(PEER)
    (card,) = cards()
    assert card["queued_from"]["name"] == "agent-b" and card["text"] == PEER
    assert steer_card()["ok"]
    assert env[-1].startswith("[sender: agent-b (session)") and env[-1].endswith(PEER)
    assert sends() == []
    assert_stays_out(exported(env[-1], sends=sends()))


def test_steer_keeps_the_original_message_record(env):
    attr = peer_attr()
    attribution.journal(attr, app="ccc", event="send")
    queue_peer(PEER, attr)
    steer_card()
    assert attribution.from_text(env[-1])["message_id"] == attr["message_id"]


@pytest.mark.parametrize("terminal", [False, True])
def test_steered_row_stays_out_on_a_real_codex_row(env, terminal):
    queue_peer(PEER)
    steer_card()
    line = {"timestamp": TS % 3, "type": "event_msg", "payload": {"type": "user_message", "message": env[-1]}}
    start = {"timestamp": TS % 0, "type": "event_msg", "payload": {"type": "user_message", "message": "Start the work"}}
    events = [codex_parse._parse_codex_event(start, 1), codex_parse._parse_codex_event(line, 2), reply()]
    assert_stays_out(steps(events, engine="codex", sends=sends(), terminal=terminal))


def test_steer_with_no_row_id_matches_by_text(env):
    queue_peer(PEER)
    assert click(text=PEER, replace_queued=True)["ok"]
    assert env[-1].startswith("[sender: agent-b")
    assert sends() == []


def test_a_row_the_queue_no_longer_holds_is_unproven(env):
    assert click(text=PEER, replace_queued=True, replace_queued_ids=["gone"])
    assert sends() == []                            # no digest, and no empty legacy send
    assert env and env[-1].startswith("[sender: unnamed (unknown)")


def test_a_row_with_no_record_is_unproven(env):
    server._queue_terminal_input(SID, PEER)         # no header, no attribution
    steer_card()
    assert sends() == []
    assert env[-1].startswith("[sender: unnamed (unknown)")
    assert_stays_out(exported(env[-1], sends=sends()))


def test_a_claimed_human_header_is_not_proof(env):
    header = f"[sender: You (human) · msg {attribution.make()['message_id']}]"
    server._queue_terminal_input(SID, header + "\n" + PEER)
    steer_card()
    assert sends() == []
    assert_stays_out(exported(env[-1], sends=sends()))


def test_your_own_queued_message_still_counts_after_steer(env):
    queue_typed("Fix the broken footer")
    assert steer_card()["ok"]
    assert env[-1] == "Fix the broken footer"
    assert any("footer" in t for t in exported(env[-1], sends=sends()))


def test_your_own_row_that_lost_its_record_is_proven_by_the_journal(env):
    attribution.journal(attribution.dashboard_human(SID, "Fix the broken footer"), app="ccc", event="inject")
    server._queue_terminal_input(SID, "Fix the broken footer")
    steer_card()
    assert env[-1] == "Fix the broken footer"
    assert any("footer" in t for t in exported(env[-1], sends=sends()))


# ---- Steer all ----------------------------------------------------------------

def test_steer_all_from_one_sender_keeps_that_sender(env):
    queue_peer(PEER)
    queue_peer(OTHER)
    assert steer_all()["ok"]
    assert env[-1].startswith("[sender: agent-b (session)")
    assert sends() == []
    assert_stays_out(exported(env[-1], sends=sends()))


def test_steer_all_from_two_senders_names_neither(env):
    queue_peer(PEER)
    queue_peer(OTHER, peer_attr("agent-c", "01a19455-2e62-72d1-bfc5-929aae0cceff"))
    steer_all()
    assert env[-1].startswith("[sender: unnamed (unknown)")
    assert_stays_out(exported(env[-1], sends=sends()))


def test_steer_all_mixing_you_and_a_peer_stays_out(env):
    queue_typed("Fix the broken footer")
    queue_peer(PEER)
    before = len(sends())
    steer_all()
    assert len(sends()) == before
    assert env[-1].startswith("[sender: unnamed (unknown)")
    assert_stays_out(exported(env[-1], sends=sends()))


def test_steer_all_of_your_own_messages_is_proven_as_joined(env):
    queue_typed("Fix the broken footer")
    queue_typed("Then fix the header")
    steer_all()
    assert not env[-1].startswith("[sender:")
    assert any("footer" in t and "header" in t for t in exported(env[-1], sends=sends()))


# ---- Steer on a delivered row -------------------------------------------------

def test_delivered_peer_row_keeps_its_sender(env):
    attr = peer_attr()
    attribution.journal(attr, app="ccc", event="send")
    click(text=PEER, steer_existing=True, steer_origin_msg_id=attr["message_id"])
    assert env[-1].startswith("[sender: agent-b (session)")
    assert sends() == []


def test_delivered_row_with_no_known_sender_is_unproven(env):
    click(text=PEER, steer_existing=True)
    assert env[-1].startswith("[sender: unnamed (unknown)")
    assert sends() == []


def test_delivered_row_you_sent_stays_yours(env):
    attribution.journal(attribution.dashboard_human(SID, "Fix the broken footer"), app="ccc", event="inject")
    click(text="Fix the broken footer", steer_existing=True)
    assert env[-1] == "Fix the broken footer"


def test_a_message_typed_in_steer_mode_is_still_typing(env):
    click(text="Fix the broken footer")
    assert env[-1] == "Fix the broken footer"
    assert any("footer" in t for t in exported(env[-1], sends=sends()))


# ---- Rows Claude Code marks as not typed --------------------------------------

@pytest.mark.parametrize("flag", ["verifiedSlackHumanTurn", "projectsUserTurn", "emailSenderTurn",
                                  "textNotTyped", "handedOff", "queueTranscriptOnly"])
def test_claude_code_not_typed_marks_stay_out(flag):
    rows = [CLI_START, claude_row(PEER, 1, entrypoint="cli", turnOrigin="human", origin={"kind": "human"}, **{flag: True})]
    assert_stays_out(steps(parse(rows)))


# ---- A row with no line number ------------------------------------------------

def test_a_no_line_row_proves_nothing():
    digest = attribution.prompt_digest("typed from the dashboard")
    events = [typed_row("Start the work"),
              {"type": "user_text", "text": "typed from the dashboard", "ts": TS % 1, "turn_origin": "sdk"},
              {"type": "user_text", "text": PEER, "ts": TS % 2, "turn_origin": "sdk"}, reply()]
    assert_stays_out(steps(events, sends=[(EPOCH + 1, digest)]))


# ---- Time join ----------------------------------------------------------------

def test_time_join_refuses_to_pick_between_two_rows():
    events = [typed_row("Start the work"),
              row("the real typed follow up", 2, 8, turn_origin="sdk"),
              row(PEER, 3, 4, turn_origin="sdk"), reply()]
    assert_stays_out(steps(events, sends=[(EPOCH + 3, "")]))


def test_time_join_still_binds_a_single_row():
    events = [typed_row("Start the work"), row("the real typed follow up", 2, 5, turn_origin="sdk"), reply()]
    assert any("follow up" in t for t in steps(events, sends=[(EPOCH + 3, "")]))


# ---- Redaction ----------------------------------------------------------------

@pytest.mark.parametrize("text", ["u=admin p=orange window", "user=admin p = orange window",
                                  "the key is: the orange window"])
def test_labelled_ordinary_word_passwords_stay_masked(text):
    got = steps([typed_row(text), reply()])
    assert got is None or not any("orange" in t.casefold() for t in got), got


@pytest.mark.parametrize("text", ["p = 0.05 after the correction", "The key is to retry the request"])
def test_ordinary_prose_is_left_alone(text):
    assert steps([typed_row(text), reply()]) == [text]


# ---- Leading slash ------------------------------------------------------------

def test_a_path_first_peer_message_gets_a_header():
    out = attribution.carry("/tmp/report.md has the " + WORD + " numbers", peer_attr(), app="ccc")
    assert out.startswith("[sender: agent-b")
    rows = [CLI_START, claude_row(out, 1, entrypoint="cli", turnOrigin="human", origin={"kind": "human"}, promptSource="typed")]
    assert_stays_out(steps(parse(rows)))


@pytest.mark.parametrize("text", ["/compact", "/plugin:skill now", "  /review this"])
def test_a_real_command_still_goes_out_bare(text):
    assert attribution.carry(text, peer_attr(), app="ccc") == text
