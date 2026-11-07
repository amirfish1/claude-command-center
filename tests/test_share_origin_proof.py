"""Share this run: who wrote a row, and what stays out when nobody can say.

Shapes from the third independent review, plus the proof that lets a prompt typed
in the dashboard through. All data is invented. A refused export counts as safe.
"""
import json
import os
import time

import pytest

import sender_envelope
import server
from ccc_server import codex_cloud_threads, engines, grok, hermes, share_run
from ccc_server.aider import _aider_assistant_event
from ccc_server.share_redact import RedactionError, Redactor

TS = "2026-01-01T00:00:00Z"
EPOCH = 1767225600.0   # TS as a number
THIS_MONTH = time.strftime("%Y-%m", time.gmtime())
ANCHOR = "typed anchor"


@pytest.fixture(autouse=True)
def no_real_hide_list(monkeypatch):
    monkeypatch.setattr(share_run, "_user_hide_list", lambda: [])


def html_of(events, engine="claude", **kw):
    """The exported page, or None when the export refuses (safe)."""
    try:
        replay, _ = share_run.build_replay(events, engine=engine, identity=kw.pop("identity", {}), **kw)
    except RedactionError:
        return None
    return share_run.render_html(replay).casefold()


def assert_not_exported(events, words, engine="claude", **kw):
    page = html_of(events, engine, **kw)
    if page is None:
        return
    found = [w for w in words if w.casefold() in page]
    assert not found, f"reached the exported HTML: {found}"


def prompts(events, engine="claude", **kw):
    """The prompts on the page, beside one typed row so the session isn't refused."""
    typed = hermes_row(ANCHOR, "desktop") if engine == "hermes" else user(ANCHOR)
    replay, _ = share_run.build_replay([*events, typed], engine=engine, identity={}, **kw)
    return [s["text"] for s in replay["steps"] if s["kind"] == "user" and s["text"] != ANCHOR]


def user(text, **extra):
    return {"type": "user_text", "entrypoint": "cli", "text": text, "ts": TS, **extra}


def reply(text):
    return {"type": "assistant", "ts": TS, "message_id": "m1", "blocks": [{"kind": "text", "text": text}]}


def sdk(text, line, seconds=0):
    ts = f"2026-01-01T00:{seconds // 60:02d}:{seconds % 60:02d}Z"
    return {"type": "user_text", "text": text, "ts": ts, "line": line, "turn_origin": "sdk"}


def hermes_row(content, source, **msg):
    return hermes._parse_hermes_message(
        {"role": "user", "content": content, "timestamp": EPOCH, **msg}, 1,
        {"source": source, "model": "x"})


def hermes_reply(content, source):
    return hermes._parse_hermes_message(
        {"role": "assistant", "content": content, "timestamp": EPOCH, "id": 2}, 2,
        {"source": source, "model": "x"})


# ---- other people's chat messages -------------------------------------------

@pytest.mark.parametrize("source", [
    "whatsapp", "whatsapp_cloud", "telegram", "bluebubbles", "cron", "subagent", "", "future-platform",
])
@pytest.mark.parametrize("content", [
    "please move my tuesday lesson, my daughter has a doctor visit",
    "[Person] please move my tuesday lesson, my daughter has a doctor visit",
])
def test_hermes_session_from_anywhere_else_is_refused(source, content):
    events = [hermes_row(content, source), hermes_reply("moved it for your daughter", source)]
    with pytest.raises(share_run.ChatBridgeSession):
        share_run.build_replay(events, engine="hermes", identity={})


def test_hermes_cli_is_not_proof_on_its_own():
    """Worker profiles run `hermes -z` with forwarded chat text. Those are `cli` too."""
    events = [hermes_row("a client wrote about her daughter", "cli"), hermes_reply("noted", "cli")]
    with pytest.raises(share_run.ChatBridgeSession):
        share_run.build_replay(events, engine="hermes", identity={})


def test_hermes_cli_with_only_notes_and_replies_is_refused():
    events = [hermes_row("[System: note]", "cli"), hermes_reply("the daughter is fine", "cli")]
    with pytest.raises(share_run.ChatBridgeSession):
        share_run.build_replay(events, engine="hermes", identity={})


def test_hermes_cli_row_sent_from_the_dashboard_exports():
    events = [hermes_row("fix the login page", "cli"), hermes_reply("done", "cli")]
    sends = [(EPOCH, sender_envelope.prompt_digest("fix the login page"))]
    assert prompts(events, engine="hermes", sends=sends) == ["fix the login page"]


def test_hermes_cli_with_one_unproven_row_is_refused():
    events = [hermes_row("fix the login page", "cli"), hermes_row("forwarded client note", "cli")]
    events[1]["line"] = 2
    sends = [(EPOCH, sender_envelope.prompt_digest("fix the login page"))]
    with pytest.raises(share_run.ChatBridgeSession):
        share_run.build_replay(events, engine="hermes", identity={}, sends=sends)


def test_hermes_lineage_with_a_chat_segment_is_refused():
    events = [hermes_row("fix the login page", "desktop"), hermes_row("from a contact", "telegram")]
    with pytest.raises(share_run.ChatBridgeSession):
        share_run.build_replay(events, engine="hermes", identity={})


def test_hermes_row_without_a_source_is_refused():
    event = user("from somewhere")
    with pytest.raises(share_run.ChatBridgeSession):
        share_run.build_replay([event], engine="hermes", identity={})


def test_hermes_desktop_prompt_exports():
    events = [hermes_row("fix the login page", "desktop"), hermes_reply("done", "desktop")]
    assert prompts(events, engine="hermes") == ["fix the login page"]


@pytest.mark.parametrize("row", [
    {"content": "[IMPORTANT: background process finished]"},
    {"content": "the delegated work finished", "display_kind": "async_delegation_complete"},
    {"content": "keep going", "display_kind": "auto_continue"},
])
def test_hermes_desktop_machine_rows_stay_out(row):
    event = hermes_row(row.pop("content"), "desktop", **row)
    assert prompts([event], engine="hermes") == []


def test_refused_export_says_why_and_returns_nothing(monkeypatch):
    monkeypatch.setattr(sender_envelope, "human_sends", lambda _sid, _months: [])
    events = [hermes_row("my daughter", "whatsapp")]
    payload, status = share_run.export_conversation(
        "s", parse=lambda _sid: {"events": events}, transcript_path=None, engine="hermes")
    assert status == 422 and payload["ok"] is False
    assert "can't prove" in payload["error"]
    assert "html" not in payload and "daughter" not in json.dumps(payload)


def test_claude_channel_row_refuses_the_session():
    ev = {"type": "user", "timestamp": TS, "message": {"role": "user", "content":
          '<channel source="imessage" chat_id="1">please call me about the lawsuit</channel>'}}
    parsed = server._parse_conversation_event(ev, 1)
    with pytest.raises(share_run.ChatBridgeSession):
        share_run.build_replay([parsed, reply("I will call about the lawsuit")], engine="claude", identity={})


def test_claude_channel_origin_refuses_the_session():
    ev = {"type": "user", "timestamp": TS, "origin": {"kind": "channel"},
          "message": {"role": "user", "content": "please call me about the lawsuit"}}
    parsed = server._parse_conversation_event(ev, 1)
    with pytest.raises(share_run.ChatBridgeSession):
        share_run.build_replay([parsed], engine="claude", identity={})


def test_a_talk_about_rss_channels_is_not_a_bridge():
    assert prompts([user("<channel> needs a title element")]) == ["<channel> needs a title element"]


@pytest.mark.parametrize("engine", ["claude", "codex", "kimi"])
def test_relay_message_refuses_the_session_on_any_engine(engine):
    text = "[relay] New message(s) from Contact:\n\n[10:00] CONTACT: about the lawsuit\n\nReview."
    with pytest.raises(share_run.ChatBridgeSession):
        share_run.build_replay([user(text)], engine=engine, identity={})



@pytest.mark.parametrize("text", [
    "[From a remote client] the lawsuit", "[Sent from a remote client through CCC Cloud Relay] the lawsuit",
])
def test_remote_client_rows_stay_out_even_without_attribution(text):
    assert_not_exported([user(text)], ["lawsuit"])


# ---- proof of a dashboard send ----------------------------------------------

def test_sdk_row_with_no_send_stays_out():
    """A dispatched prompt has the same mark as a dashboard send."""
    assert prompts([sdk("do the private dispatched task", 1)]) == []


def test_sdk_row_matching_a_send_exports():
    sends = [(EPOCH, sender_envelope.prompt_digest("fix the login page"))]
    assert prompts([sdk("fix  the login\npage", 1)], sends=sends) == ["fix the login\npage"]


def test_one_send_proves_one_row():
    sends = [(EPOCH, sender_envelope.prompt_digest("continue"))]
    assert prompts([sdk("continue", 1), sdk("continue", 2)], sends=sends) == ["continue"]


def test_send_for_other_text_proves_nothing():
    sends = [(EPOCH, sender_envelope.prompt_digest("something else"))]
    assert prompts([sdk("first", 1), sdk("fix the login page", 2)], sends=sends) == []


def test_a_digest_never_vouches_for_a_marked_row():
    digest = sender_envelope.prompt_digest("from another session")
    row = dict(sdk("from another session", 1), peer={"name": "x"})
    assert prompts([row], sends=[(EPOCH, digest)]) == []


def test_old_send_joins_by_time_but_never_the_first_row():
    rows = [sdk("dispatched prompt", 1), sdk("fix the login page", 2, seconds=2)]
    assert prompts(rows, sends=[(EPOCH, ""), (EPOCH + 1, "")]) == ["fix the login page"]


def test_old_send_does_not_reach_past_the_window():
    rows = [sdk("dispatched prompt", 1), sdk("much later", 2, seconds=40)]
    assert prompts(rows, sends=[(EPOCH + 5, "")]) == []


def test_old_send_proves_one_row():
    rows = [sdk("first", 1), sdk("mine", 2, seconds=2), sdk("not mine", 3, seconds=59)]
    assert prompts(rows, sends=[(EPOCH + 1, "")]) == ["mine"]


def test_old_send_with_two_candidates_proves_neither():
    rows = [sdk("first", 1), sdk("mine", 2, seconds=2), sdk("not mine", 3, seconds=3)]
    assert prompts(rows, sends=[(EPOCH + 1, "")]) == []


def test_a_new_send_does_not_use_the_time_join():
    rows = [sdk("first", 1), sdk("not what was typed", 2, seconds=2)]
    sends = [(EPOCH + 1, sender_envelope.prompt_digest("what was typed"))]
    assert prompts(rows, sends=sends) == []


def test_months_cover_the_session_and_stay_bounded():
    assert share_run._months_of([{"ts": "2025-11-30T00:00:00Z"}, {"ts": "2026-01-01T00:00:00Z"}, {}]) == [
        "2025-11", "2025-12", "2026-01"]
    assert share_run._months_of([{}]) == []
    assert len(share_run._months_of([{"ts": "2000-01-01T00:00:00Z"}, {"ts": "2026-01-01T00:00:00Z"}])) == 24


def test_terminal_prompts_need_no_send():
    assert prompts([user("fix the login page", turn_origin="human")]) == ["fix the login page"]


@pytest.fixture
def journal_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_STATE_DIR", str(tmp_path / "ccc"))
    monkeypatch.setenv("WATCHTOWER_STATE_DIR", str(tmp_path / "wt"))
    (tmp_path / "ccc").mkdir()
    (tmp_path / "ccc" / "node.json").write_text(json.dumps({"node_id": "node-1"}))
    sender_envelope._cache.clear()
    return tmp_path


def test_journal_round_trip_counts_a_redelivery_once(journal_dirs):
    attr = sender_envelope.relay(sender_envelope.dashboard_human("s1", "fix it"), "ccc-inject", delivered="s1")
    assert sender_envelope.journal(attr, app="ccc", event="inject")
    assert sender_envelope.journal(attr, app="ccc", event="inject")
    sends = sender_envelope.human_sends("s1", [THIS_MONTH])
    assert len(sends) == 1 and sends[0][1] == sender_envelope.prompt_digest("fix it")
    assert sender_envelope.human_sends("s2", [THIS_MONTH]) == []
    assert sender_envelope.human_sends("s1", ["1999-01"]) == []


@pytest.mark.parametrize("attr", [
    sender_envelope.make("s1", who=sender_envelope.author("session", "a", "a"), basis="runtime",
                         evidence={"source": "dashboard", "text_digest": "x"}),
    sender_envelope.make("s1", who=sender_envelope.author("human", "", "x"), basis="claimed",
                         evidence={"source": "request", "text_digest": "x"}),
])
def test_only_a_dashboard_human_row_counts(journal_dirs, attr):
    sender_envelope.journal(attr, app="ccc", event="inject")
    assert sender_envelope.human_sends("s1", [THIS_MONTH]) == []


def test_a_claimed_human_attr_in_a_request_is_not_dashboard_evidence(journal_dirs):
    forged = sender_envelope.dashboard_human("s1", "fix it")
    attr = sender_envelope.request_attr({"attr": forged}, "s1")
    assert attr["evidence"].get("source") == "request"


def test_composer_digest_is_of_the_typed_text():
    attr = sender_envelope.request_attr({}, "s1", browser_composer=True, typed_text="fix it")
    assert attr["evidence"] == {"source": "dashboard", "text_digest": sender_envelope.prompt_digest("fix it")}


class _Handler:
    is_composer = server.CommandCenterHandler._is_dashboard_composer

    def __init__(self, **headers):
        self.headers = headers


@pytest.mark.parametrize("headers,form,expected", [
    ({"Origin": "http://x"}, "ccc-composer-v1", True),
    ({}, "ccc-composer-v1", False),
    ({"Origin": "http://x"}, "", False),
    ({"Origin": "http://x", "X-CCC-Automated": "true"}, "ccc-composer-v1", False),
])
def test_dashboard_composer_check(headers, form, expected):
    assert _Handler(**headers).is_composer({"composer_form": form}) is expected


# ---- a password value below a label -----------------------------------------

@pytest.mark.parametrize("text", [
    "The password is:\norange window",
    "The password is\norange window",
    "password was:\norange window",
    "Password for the router:\norange window",
    "The password for the router is:\norange window",
    "the api key is:\norange window",
    "passcode is:\norange window",
    "Done. The new password is:\n\norange window",
    "The password is:\r\norange window",
    "> the password is:\n> orange window",
])
@pytest.mark.parametrize("maker", [user, reply])
def test_value_below_a_label_with_words_after_it(text, maker):
    assert_not_exported([maker(text)], ["orange window"])


def test_value_below_a_label_in_the_title():
    assert_not_exported([user("hello")], ["orange window"], title="The password is:\norange window")


@pytest.mark.parametrize("text", [
    "site\tuser\tpassword\tnotes\nhome\tadmin\torange window\tnone",
    "site;user;password;notes\nhome;admin;orange window;none",
    '"site","password","notes"\n"home","orange window","none"',
    "site | password | notes\nhome | orange window | none",
])
def test_table_header_shapes(text):
    assert_not_exported([user(text)], ["orange window"])


def test_a_sentence_about_passwords_keeps_the_next_paragraph():
    page = html_of([user("We should fix the password reset flow.\n\nThe tests are green.")])
    assert page is not None and "tests are green" in page


# ---- reply text that is not a reply -----------------------------------------

@pytest.mark.parametrize("part", [
    {"kind": "thinking", "value": "the customer contract looks risky here"},
    {"kind": "progressMessage", "content": {"value": "the customer contract looks risky here"}},
    {"kind": "warning", "content": {"value": "the customer contract looks risky here"}},
])
def test_copilot_chat_non_reply_parts(tmp_path, part):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"requests": [{
        "timestamp": 1767225600000, "message": {"text": "hello"},
        "response": [part, {"value": "All done."}]}]}))
    events, _ = engines._copilotchat_events_from_file(path)
    assert any(e["type"] == "assistant" for e in events)
    assert_not_exported(events, ["contract", "risky"], engine="copilot-chat")


def test_copilot_chat_keeps_reply_parts():
    text, _tools = engines._copilotchat_response_texts([
        {"value": "one"}, {"kind": "markdownContent", "content": {"value": "two"}}])
    assert text == "one\ntwo"


def test_aider_console_output_stays_out():
    raw = _aider_assistant_event("s", "Running tests\nFAILED login page shows the wrong customer balance")
    event = server._parse_conversation_event(raw, 1)
    assert_not_exported([event], ["wrong customer balance"], engine="aider")


def test_antigravity_console_log_stays_out():
    event = {"type": "assistant", "ts": "", "message_id": "antigravity-cli-3",
             "blocks": [{"kind": "text", "text": "wrong customer balance"}]}
    assert_not_exported([event], ["wrong customer balance"], engine="antigravity")


def test_grok_subagent_progress(tmp_path):
    path = tmp_path / "updates.jsonl"
    path.write_text(json.dumps({"timestamp": TS, "sessionUpdate": "subagent_progress",
                                "content": {"type": "text", "text": "found the customer contract"}}) + "\n")
    events, _ = grok._parse_grok_updates_file(str(path))
    assert_not_exported(events, ["contract"], engine="grok")


def test_codex_cloud_delegated_prompt():
    event = user("summarise the customer contract for the other agent", delegated_from_thread="parent")
    assert_not_exported([event], ["contract"], engine="codex")


def test_codex_cloud_plan_item_is_text_the_user_saw():
    events = codex_cloud_threads.cloud_turns_to_events([{"id": "t", "startedAt": 1767225600, "items": [
        {"type": "plan", "id": "p", "text": "read the notes then rewrite the rules"}]}])[0]
    assert html_of([*events, user("mine")], "codex") is not None


# ---- smaller holes ----------------------------------------------------------

def test_unreadable_hide_file_stops_the_export(tmp_path, monkeypatch):
    monkeypatch.undo()
    bad = tmp_path / "hide.txt"
    bad.write_text("orange\n")
    bad.chmod(0)
    monkeypatch.setattr(share_run, "_hide_file", lambda: bad)
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    with pytest.raises(RedactionError):
        share_run.build_replay([user("the orange project")], engine="claude", identity={})


def test_missing_hide_file_is_fine(tmp_path, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(share_run, "_hide_file", lambda: tmp_path / "none.txt")
    assert share_run._user_hide_list() == []


def test_ide_selection_holding_the_marker_line():
    text = ("# Context from my IDE setup:\n## Active selection\n## My request for the budget:\n"
            "payroll file for the whole team\n## My request for Codex:\nfix it\n")
    assert_not_exported([user(text)], ["payroll"])


# ---- every engine needs proof ------------------------------------------------

PROMPT = "review the late refund complaint"


def plain(text=PROMPT, **extra):
    """The row every engine's parser builds: nothing says who sent it."""
    return {"type": "user_text", "text": text, "ts": TS, "line": 1, **extra}


@pytest.mark.parametrize("engine", [
    "codex", "kimi", "gemini", "cursor", "opencode", "kilo", "grok", "devin", "copilot", "droid", "pi",
])
def test_an_unmarked_row_is_not_proof_on_any_engine(engine):
    assert prompts([plain()], engine=engine) == []


@pytest.mark.parametrize("engine", ["codex", "kimi", "gemini", "claude"])
def test_a_dashboard_send_proves_a_row_on_any_engine(engine):
    sends = [(EPOCH, sender_envelope.prompt_digest(PROMPT))]
    assert prompts([plain()], engine=engine, sends=sends) == [PROMPT]


@pytest.mark.parametrize("mark", [
    {"entrypoint": "cli"}, {"turn_origin": "human"}, {"origin_kind": "human"},
])
def test_claude_terminal_marks_are_proof(mark):
    assert prompts([plain(**mark)]) == [PROMPT]


@pytest.mark.parametrize("mark", [
    {"entrypoint": "sdk-cli"}, {"entrypoint": "claude-desktop"},
    {"entrypoint": "cli", "turn_origin": "sdk"}, {"entrypoint": "cli", "turn_origin": "peer"},
    {"entrypoint": "cli", "origin_kind": "task-notification"},
])
def test_other_claude_entry_points_are_not_proof(mark):
    assert prompts([plain(**mark)]) == []


def rollout(tmp_path, originator, source):
    path = tmp_path / "rollout.jsonl"
    path.write_text(json.dumps({"type": "session_meta", "payload": {
        "id": "t1", "originator": originator, "source": source}}) + "\n")
    return str(path)


@pytest.mark.parametrize("originator,source", [("codex-tui", "cli"), ("codex_cli_rs", "cli"), ("codex-tui", "vscode")])
def test_codex_terminal_session_is_recognised(tmp_path, originator, source):
    assert share_run._is_terminal_session("codex", rollout(tmp_path, originator, source))


@pytest.mark.parametrize("originator,source", [
    ("codex_exec", "exec"), ("claude-command-center", "vscode"), ("a_queue_worker", "vscode"),
    ("codex-tui", "exec"), ("codex-tui", {"subagent": "review"}), ("", "cli"),
    ("codex_cli_rs", "mcp"), ("codex-tui", "unknown"),
])
def test_codex_automation_is_not_a_terminal_session(tmp_path, originator, source):
    assert not share_run._is_terminal_session("codex", rollout(tmp_path, originator, source))


def test_terminal_check_is_codex_only_and_survives_bad_files(tmp_path):
    good = rollout(tmp_path, "codex-tui", "cli")
    assert not share_run._is_terminal_session("kimi", good)
    assert not share_run._is_terminal_session("codex", None)
    assert not share_run._is_terminal_session("codex", str(tmp_path / "missing.jsonl"))
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n")
    assert not share_run._is_terminal_session("codex", str(bad))


def test_export_keeps_codex_terminal_prompts(tmp_path, monkeypatch):
    monkeypatch.setattr(sender_envelope, "human_sends", lambda _sid, _months: [])
    monkeypatch.setattr(share_run, "transcript_cwds", lambda _path: [])
    payload, status = share_run.export_conversation(
        "s", parse=lambda _sid: {"events": [plain()]},
        transcript_path=rollout(tmp_path, "codex-tui", "cli"), engine="codex")
    assert status == 200 and payload["replay"]["stats"]["prompts"] == 1


# ---- no proven prompt, no export ---------------------------------------------

def test_replies_to_prompts_nobody_vouches_for_refuse_the_session():
    """The reply repeats what it answers, so dropping only the prompt is not enough."""
    with pytest.raises(share_run.ChatBridgeSession):
        share_run.build_replay([sdk("my client says this", 1), reply("Your client says this")],
                               engine="claude", identity={})


def test_a_session_with_no_user_row_is_refused():
    """A sub-agent rollout has replies and no user row, and another agent started it."""
    with pytest.raises(share_run.ChatBridgeSession):
        share_run.build_replay([reply("The client asked for a refund.")], engine="codex", identity={})


def test_refusal_reaches_the_export_as_a_422(monkeypatch):
    monkeypatch.setattr(sender_envelope, "human_sends", lambda _sid, _months: [])
    monkeypatch.setattr(share_run, "transcript_cwds", lambda _path: [])
    payload, status = share_run.export_conversation(
        "s", parse=lambda _sid: {"events": [sdk("dispatched", 1)]}, transcript_path=None, engine="claude")
    assert status == 422 and "replay" not in payload and "html" not in payload


def test_one_proven_prompt_keeps_the_session():
    sends = [(EPOCH, sender_envelope.prompt_digest("mine"))]
    replay, _ = share_run.build_replay([sdk("dispatched", 1), sdk("mine", 2), reply("Done.")],
                                       engine="claude", identity={}, sends=sends)
    assert [s["text"] for s in replay["steps"] if s["kind"] == "user"] == ["mine"]


# ---- marks survive every parser branch ---------------------------------------

def claude_row(content, **extra):
    return server._parse_conversation_event({
        "type": "user", "timestamp": TS, "uuid": "u1",
        "message": {"role": "user", "content": content}, **extra}, 1)


CHANNEL = '<channel source="plugin:telegram:telegram" user="neighbor">water my plants</channel>'


@pytest.mark.parametrize("kind", ["channel", "slack-ping"])
def test_a_meta_chat_row_refuses_the_session(kind):
    row = claude_row(CHANNEL, isMeta=True, origin={"kind": kind, "server": "plugin:telegram:telegram"})
    with pytest.raises(share_run.ChatBridgeSession):
        share_run.build_replay([row, user("mine"), reply("Your neighbor asked about the plants.")],
                               engine="claude", identity={})


def test_a_meta_chat_row_stays_out_of_the_conversation_pane():
    row = claude_row(CHANNEL, isMeta=True, origin={"kind": "channel"})
    assert row["type"] != "user_text" and "text" not in row


def test_other_meta_rows_stay_hidden():
    assert claude_row("caveat text", isMeta=True) is None
    assert claude_row("caveat text", isMeta=True, origin={"kind": "task-notification"}) is None


SLASH = ("<command-message>goal</command-message>\n<command-name>/goal</command-name>\n"
         "<command-args>finish the refund review</command-args>")


def test_a_dispatched_slash_command_keeps_its_mark():
    row = claude_row(SLASH, turnOrigin="sdk", entrypoint="sdk-cli")
    assert row["turn_origin"] == "sdk" and prompts([row]) == []


def test_a_typed_slash_command_exports():
    assert prompts([claude_row(SLASH, entrypoint="cli")]) == ["[path] finish the refund review"]


def queued_row(**extra):
    return server._parse_conversation_event({
        "type": "attachment", "timestamp": TS, "attachment": {
            "type": "queued_command", "commandMode": "prompt", "prompt": PROMPT}, **extra}, 1)


def test_a_queued_row_needs_proof_like_any_other():
    assert prompts([queued_row(entrypoint="sdk-cli")]) == []
    sends = [(EPOCH, sender_envelope.prompt_digest(PROMPT))]
    assert prompts([queued_row(entrypoint="sdk-cli")], sends=sends) == [PROMPT]


def test_a_queued_row_in_a_terminal_session_exports():
    assert prompts([queued_row(entrypoint="cli")]) == [PROMPT]


def queued_with(text=PROMPT, origin=None, **attachment):
    return server._parse_conversation_event({
        "type": "attachment", "timestamp": TS, "entrypoint": "cli", "attachment": {
            "type": "queued_command", "commandMode": "prompt", "prompt": text,
            **({"origin": origin} if origin else {}), **attachment}}, 1)


@pytest.mark.parametrize("kind", ["coordinator", "plugin", "observer", "unclassified"])
def test_a_queued_row_from_a_non_person_stays_out_of_a_terminal_session(kind):
    assert prompts([queued_with(origin={"kind": kind})]) == []


def test_a_queued_meta_row_stays_out_of_a_terminal_session():
    assert prompts([queued_with(isMeta=True)]) == []


@pytest.mark.parametrize("entrypoint", ["cli", "sdk-cli"])
@pytest.mark.parametrize("prefix", ["", "[MESSAGE FROM NON-USER SOURCE - NOT USER INPUT]\n"])
def test_a_queued_slack_ping_refuses_the_session(entrypoint, prefix):
    row = queued_with(prefix + "the invoice is wrong", origin={"kind": "slack-ping"})
    row["entrypoint"] = entrypoint
    with pytest.raises(share_run.ChatBridgeSession):
        share_run.build_replay([user("mine"), row, reply("Fixing the invoice.")], engine="claude", identity={})


def test_a_non_user_source_marker_refuses_the_session_with_no_origin():
    row = queued_with("[MESSAGE FROM NON-USER SOURCE - NOT USER INPUT]\nthe invoice is wrong")
    with pytest.raises(share_run.ChatBridgeSession):
        share_run.build_replay([user("mine"), row, reply("Fixing it.")], engine="claude", identity={})


def test_every_user_row_of_a_hermes_script_session_needs_proof():
    """Even a bracketed one, which is otherwise skipped as a machine note."""
    mine, other = hermes_row("fix it", "cli"), hermes_row("[Neighbor] move my lesson", "cli")
    mine["line"], other["line"] = 1, 2
    sends = [(EPOCH, sender_envelope.prompt_digest("fix it"))]
    with pytest.raises(share_run.ChatBridgeSession):
        share_run.build_replay([mine, other], engine="hermes", identity={}, sends=sends)


def test_a_journal_row_with_a_bad_recipient_does_not_stop_the_reader(tmp_path, monkeypatch):
    entry = {"at": 0, "human": {}, "messages": {}}
    sender_envelope._journal_add(entry, json.dumps({"attr": {"delivered_recipient": "bad"}}), set())
    assert entry["human"] == {}


# ---- passwords in ordinary words ---------------------------------------------

@pytest.mark.parametrize("text", [
    "wifi: orange window",
    "The wifi is orange window",
    "Pasword: orange window",
    "passowrd is orange window",
    "log in with admin and orange window",
    "u: admin p: orange window",
    "p/w: orange window",
    "p/w orange window",
    "admin / orange window",
    "orange window, that's the password",
    "Your password has been set.\n\norange window",
    "the key is orange window",
    "the code word is orange window",
    "vault key orange window",
    "signing key orange window",
])
def test_password_in_ordinary_words_is_hidden(text):
    assert "orange" not in Redactor().text(text, 400).casefold()


@pytest.mark.parametrize("text", [
    "The key is to retry the request after a short pause in the loop",
    "The key is to retry",
    "p = subprocess.run(args)",
    "read / write access for the team",
    "git log in the repo",
    "step: run the tests",
])
def test_ordinary_sentences_keep_their_words(text):
    assert "[secret]" not in Redactor().text(text, 400)


@pytest.mark.xfail(reason="Known limit in docs/sharing.md: nothing marks these words as a password", strict=True)
def test_known_limit_a_bare_sudo_password():
    assert "orange" not in Redactor().text("sudo orange window", 400).casefold()


def test_a_password_heading_does_not_hide_the_paragraph_under_it():
    assert "the flow" in Redactor().text("Password reset\nthe flow sends a mail", 400)

