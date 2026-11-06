"""Leak shapes found by independent review of Share this run.

All data is invented. Nothing here reads a real transcript or contacts a service.
A RedactionError counts as safe everywhere: the export was refused.
"""
import json
import re
import sqlite3

import pytest

from ccc_server import share_run
from ccc_server.share_redact import RedactionError, Redactor

TS = "2026-01-01T00:00:00Z"
PLACEHOLDER = re.compile(r"\[[a-z]+(?:-\d+)?(?:, \d+ lines?)?\]")


@pytest.fixture(autouse=True)
def no_real_hide_list(monkeypatch):
    # The real share-hide.txt must not be read or change results.
    monkeypatch.setattr(share_run, "_user_hide_list", lambda: [])


def replay_of(events, engine="claude", **kw):
    identity = kw.pop("identity", {})
    replay, _ = share_run.build_replay(events, engine=engine, identity=identity, **kw)
    return replay


def page_text(replay):
    return " || ".join(
        [replay.get("title") or ""]
        + [s.get("text") or "" for s in replay["steps"]]
    )


def exported(text, **kw):
    """Full path: masks, then verify(). None means the export was refused."""
    try:
        return page_text(replay_of([{"type": "user_text", "text": text, "ts": TS}], **kw))
    except RedactionError:
        return None


def assert_gone(text, values, **kw):
    out = exported(text, **kw)
    if out is None:
        return
    left = [v for v in values if v in out]
    assert not left, f"survived {left} in {out!r}"


def digits_of(out):
    return re.sub(r"\D", "", PLACEHOLDER.sub(" ", out))


# ---------------------------------------------------------------------------
# M1. Card, SSN and phone numbers split by something other than a known gap.
# docs/sharing.md: "numbers split into three or more groups ... are hidden".
# Cause: _DIGIT_GROUPS_RE and _VERIFY_DIGITS_RE. verify() excludes newlines and
# allows a gap of 4 characters at most.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "4111\n\n1111\n\n1111\n\n1111",
    "- 4111\n- 1111\n- 1111\n- 1111",
    "1. 4111\n2. 1111\n3. 1111\n4. 1111",
    "**4111** **1111** **1111** **1111**",
    "`4111` - `1111` - `1111` - `1111`",
    "4111<br/>1111<br/>1111<br/>1111",
    "4111 \\\n1111 \\\n1111 \\\n1111",
    '"4111" , "1111" , "1111" , "1111"',
    "first 4111, then 1111, then 1111, then 1111",
    "(4111) (1111) (1111) (1111)",
])
def test_leak_m1_card_number(text):
    assert_gone(text, ["1111"])


@pytest.mark.parametrize("text,value", [
    ("ssn 123\n\n45\n\n6789", "6789"),
    ("123*45*6789", "6789"),
    ("555*867*5309", "5309"),
    ("555 ~ 867 ~ 5309", "5309"),
    ("555 x 867 x 5309", "5309"),
    ("555️ 867️ 5309", "5309"),
])
def test_leak_m1_ssn_or_phone(text, value):
    assert_gone(text, [value])


# ---------------------------------------------------------------------------
# M2. Wallet seed phrase in any shape but the few gaps _SEED_GAP_RE allows.
# verify() calls the same _seed_runs, so it is blind in exactly the same places.
# ---------------------------------------------------------------------------
SEED = "abandon ability able about above absent absorb abstract absurd abuse access accident".split()


def assert_seed_gone(text):
    out = exported(text)
    if out is None:
        return
    readable = [w for w in SEED if re.search(rf"\b{w}\b", out)]
    assert len(readable) < 3, f"{len(readable)} of 12 seed words readable: {out!r}"


@pytest.mark.parametrize("text", [
    json.dumps(SEED),
    ", ".join(f"'{w}'" for w in SEED),
    " | ".join(SEED),
    "| " + " | ".join(SEED) + " |",
    " / ".join(SEED),
    " · ".join(SEED),
    " • ".join(SEED),
    " → ".join(SEED),
    " + ".join(SEED),
    " ".join(f"**{w}**" for w in SEED),
    " ".join(f"`{w}`" for w in SEED),
    "\n".join(f"* {w}" for w in SEED),
    "\n".join(f"({i}) {w}" for i, w in enumerate(SEED, 1)),
    "seed phrase: " + " | ".join(SEED),
    "recovery words: " + " ".join(f"**{w}**" for w in SEED),
    "️ ".join(SEED),
])
def test_leak_m2_seed_phrase(text):
    assert_seed_gone(text)


# ---------------------------------------------------------------------------
# M3. Plain-word password after a label.
# docs/sharing.md: "whatever follows a password-style label or flag is hidden,
# up to the end of the clause, even if it is plain words".
# Value is "orange window": both are on the allow-list, so only the label rule
# can hide them. Cause: _LABEL_VALUE_RE, _BARE_PASSWORD_RE, _VERIFY_LABEL_RE.
# ---------------------------------------------------------------------------
V = "orange window"


@pytest.mark.parametrize("text", [
    # markdown around the label
    f"**password**: {V}",
    f"*password*: {V}",
    f"`password`: {V}",
    f"| password | {V} |",
    f"__password__: {V}",
    f"<b>password</b>: {V}",
    f"<password>{V}</password>",
    # separators the label rule does not know, or takes as the value itself
    f"password - {V}",
    f"password — {V}",
    f"password – {V}",
    f"password -> {V}",
    f"password → {V}",
    # value on the next line
    f"password:\n{V}",
    f"password\n{V}",
    f"password =\n{V}",
    f"**Password:**\n{V}",
    # natural sentences
    f"password's {V}",
    f"the password I set is {V}",
    f"the password I picked was {V}",
    f"the password they gave me is {V}",
    f"the password we use is {V}",
    f"password set to {V}",
    f"password changed to {V}",
    f"password reset to {V}",
    f"password now {V}",
    f"password should now be {V}",
    f"password has been changed to {V}",
    f"password here: {V}",
    f"password below: {V}",
    f"password for my old work laptop at home is {V}",
    f"changed the password to {V}",
    f"password {V}",
    f"wifi password {V}",
    # label variants
    f"mypassword is {V}",
    f"dbpassword is {V}",
    f"pass word: {V}",
    f"pass word is {V}",
    f"pass  phrase: {V}",
    f"pass.phrase: {V}",
    f"pass key: {V}",
    f"secret word: {V}",
    f"user admin pass {V}",
    f"<secret>{V}</secret>",
    f"set_password('{V}')",
    f"token {V}",
    f"API key {V}",
    # value punctuation stops the clause early
    "password: orange, window",
    "secret: orange, window",
    "password: 'don't open window'",
    # plain lookalikes
    f"\x1b[31mpassword\x1b[0m: {V}",
    f"pass️word: {V}",
])
def test_leak_m3_password_value(text):
    assert_gone(text, ["orange", "window"])


# ---------------------------------------------------------------------------
# M4. PIN or door code. Fix R2 covers "code to the safe is 4821" and only that.
# Cause: _PIN_RE needs the exact verb and a bare digit run.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "code to the safe: 4821",
    "door code on the left: 4821",
    "code for the garage door: 4821",
    'the code to the safe is "4821"',
    "the code to the safe is **4821**",
    "the code to the safe is `4821`",
    "the code to the safe is now 4821",
    "the code to the safe was set to 4821",
    "the code to the safe = 4821",
    "code for the side gate 4821",
    "pin for my mother's bank account at work: 4821",
    "pin:\n4821",
    "pin\n4821",
    "passcode:\n4821",
    "pin 48 21",
    "pin 4 8 2 1",
    "my pin 4-8-2-1",
    "the combination is 48-21",
    "combination is 4821",
    "4821 is my code",
])
def test_leak_m4_pin(text):
    out = exported(text)
    if out is None:
        return
    assert "4821" not in digits_of(out), out


# ---------------------------------------------------------------------------
# M5. Content the docs list as Left out, reaching the export through the real
# parser. Redaction runs on it, but the point of leaving it out is that plain
# words survive redaction.
# ---------------------------------------------------------------------------
def _sqlite_session():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("create table message(id text,session_id text,data text,time_created int)")
    con.execute("create table part(message_id text,data text,time_created int)")
    con.execute("insert into message values('m1','s1',?,1)",
                (json.dumps({"role": "user", "time": {"created": 1000}}),))
    con.execute("insert into part values('m1',?,1)",
                (json.dumps({"type": "text", "text": "fix it"}),))
    con.execute("insert into message values('m2','s1',?,2)",
                (json.dumps({"role": "assistant", "time": {"created": 2000}, "modelID": "x"}),))
    con.execute("insert into part values('m2',?,1)",
                (json.dumps({"type": "reasoning", "text": "private thinking about the orange lawsuit"}),))
    con.execute("insert into part values('m2',?,2)",
                (json.dumps({"type": "text", "text": "done"}),))
    con.commit()
    return con


@pytest.fixture(scope="module")
def server_module():
    import server
    return server


def test_leak_m5_opencode_reasoning(server_module, monkeypatch):
    from ccc_server import opencode
    monkeypatch.setattr(opencode, "_opencode_connect", _sqlite_session)
    events = opencode._parse_opencode_conversation("s1")["events"]
    assert "lawsuit" not in page_text(replay_of(events, engine="opencode"))


def test_leak_m5_kilo_reasoning(server_module, monkeypatch):
    from ccc_server import kilo
    monkeypatch.setattr(kilo, "_kilo_connect", _sqlite_session)
    events = kilo._parse_kilo_conversation("s1")["events"]
    assert "lawsuit" not in page_text(replay_of(events, engine="kilo"))


@pytest.mark.parametrize("tag", ["stdout", "stderr"])
def test_leak_m5_claude_local_command_output(server_module, tag):
    rows = [
        {"type": "user", "timestamp": TS, "message": {"role": "user", "content": "go"}},
        {"type": "system", "subtype": "local_command", "uuid": "u1", "timestamp": TS,
         "content": f"<local-command-{tag}>orange lawsuit output</local-command-{tag}>"},
    ]
    events = [e for e in (server_module._parse_conversation_event(r, i) for i, r in enumerate(rows)) if e]
    assert "lawsuit" not in page_text(replay_of(events))


ENVELOPE = "[sender: Other Session (session) · msg abc123]\npeer orange lawsuit note"


def test_leak_m5_peer_message_claude_no_journal_entry(server_module):
    row = {"type": "user", "timestamp": TS, "message": {"role": "user", "content": ENVELOPE}}
    events = [server_module._parse_conversation_event(row, 0)]
    assert "lawsuit" not in page_text(replay_of(events))


def test_leak_m5_peer_message_codex_no_journal_entry(server_module):
    from ccc_server import codex_parse
    row = {"type": "event_msg", "timestamp": TS, "payload": {"type": "user_message", "message": ENVELOPE}}
    events = [codex_parse._parse_codex_event(row, 0)]
    assert "lawsuit" not in page_text(replay_of(events, engine="codex"))


def test_leak_m5_queued_peer_message():
    # Shape copied from pending_inputs._get_queued_events_for_session, which
    # parse_conversation merges in.
    events = [
        {"type": "user_text", "text": "ok", "ts": TS},
        {"line": None, "ts": TS, "type": "user_text", "images": [], "pending": True,
         "text": "note from the other agent about the orange lawsuit",
         "queued_from": {"kind": "session", "name": "other"}},
    ]
    assert "lawsuit" not in page_text(replay_of(events))


# Shapes copied from the parsers, not run through them.
def test_leak_s1_devin_subagent_report():
    events = [{"type": "assistant", "ts": TS, "subagent_report": True, "actor": "sidekick",
               "blocks": [{"kind": "text", "text": "report about the orange lawsuit"}]}]
    assert "lawsuit" not in page_text(replay_of(events, engine="devin"))


def test_leak_s1_hermes_private_planning():
    events = [{"ts": TS, "type": "assistant", "subtype": "hermes_pending_planning",
               "source_platform": "whatsapp", "text": "plan about the orange lawsuit",
               "blocks": [{"kind": "text", "text": "plan about the orange lawsuit"}]}]
    with pytest.raises(share_run.ChatBridgeSession):
        replay_of(events, engine="hermes")


# ---------------------------------------------------------------------------
# S2. "Also hide" does not always win. Docs: "Words you list always apply".
# Summit-like word used here: "monkey", on the allow-list.
# ---------------------------------------------------------------------------
def test_leak_s2_keep_beats_hide():
    assert_gone("we paid monkey today", ["monkey"], hide=["monkey"], keep=["monkey"])


def test_leak_s2_item_51_silently_dropped():
    terms = ",".join(f"word{chr(97 + i // 26)}{chr(97 + i % 26)}" for i in range(60))
    hide = share_run.parse_list(terms + ",monkey")
    assert_gone("we paid monkey today", ["monkey"], hide=hide)


def test_leak_s2_long_item_silently_cut():
    hide = share_run.parse_list("a" * 90 + " monkey")
    out = exported("x " + "a" * 90 + " monkey", hide=hide)
    assert out is None or "monkey" not in out, out


@pytest.mark.parametrize("text,hide,value", [
    ("we paid mon️key today", "monkey", "key"),
    ("the monkeys and the monkeyed", "monkey", "monkeys"),
    ("send my resume", "Résumé", "resume"),
    ("build v1.2.3 shipped", "1.2.3", "1.2"),
])
def test_leak_s2_hide_variant(text, hide, value):
    assert_gone(text, [value], hide=[hide])


def _tool_events():
    return [
        {"type": "user_text", "text": "go", "ts": TS},
        {"type": "assistant", "ts": TS, "model": "x", "blocks": [
            {"kind": "tool_use", "name": "Bash", "command": "terraform apply", "id": "a"}]},
    ]


def test_leak_s2_hide_ignored_in_tool_labels():
    try:
        html = share_run.render_html(replay_of(_tool_events(), hide=["terraform"]))
    except RedactionError:
        return
    assert "terraform" not in html


# ---------------------------------------------------------------------------
# S3. Variation selectors and tag characters are not stripped (_INVISIBLE_RE).
# Covered by the ️ cases in M1 to M3 and S2.
#
# S4. Survivor chips. Docs: "the dialog lists every capitalized word". Words
# after a sentence end, colon or bracket are dropped, so the review list hides
# the brand names plain English lets through.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,word", [
    ("Target pays late.", "Target"),
    ("Client: Target", "Target"),
    ("see (Google) now", "Google"),
    ("**Target** is the client", "Target"),
    ("# Target rollout", "Target"),
    ("- Target\n- Google", "Target"),
])
def test_leak_s4_survivor_list_misses_readable_name(text, word):
    replay = replay_of([{"type": "user_text", "text": text, "ts": TS}])
    assert word in page_text(replay)   # readable
    assert word in [s["word"] for s in share_run.survivors(replay)]


# ---------------------------------------------------------------------------
# S5. Repo and folder names are only protected when the transcript file holds a
# "cwd". SQLite engines (OpenCode, Kilo, Hermes) pass no such file.
# ---------------------------------------------------------------------------
def test_leak_s5_folder_name_without_cwd_in_transcript():
    events = [{"type": "user_text", "text": "the orange-window repo is broken", "ts": TS}]
    payload, status = share_run.export_conversation(
        "x", parse=lambda _: {"events": events}, transcript_path="", engine="opencode",
        cwd="/tmp/x/orange-window",
    )
    assert status == 200
    assert "orange-window" not in page_text(payload["replay"])


def test_holds_folder_name_with_cwd_in_transcript(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({"cwd": "/tmp/x/orange-window"}) + "\n")
    events = [{"type": "user_text", "text": "the orange-window repo is broken", "ts": TS}]
    identity = share_run.machine_identity(share_run.transcript_cwds(str(path)))
    replay, _ = share_run.build_replay(events, engine="claude", identity=identity)
    assert "orange-window" not in page_text(replay)


# ---------------------------------------------------------------------------
# S6. Birth dates. R4 fixed "born 1985"; other forms remain.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "born back in 1985",
    "born in the year 1985",
    "birth year: 1985",
    "year of birth: 1985",
    "YOB 1985",
    "DOB: **1985**",
    "born:\n1985",
    "born: `1985`",
    "nacido el 14 de marzo de 1985",
    "date de naissance: 14 mars 1985",
])
def test_leak_s6_birth_year(text):
    assert_gone(text, ["1985"])


# ---------------------------------------------------------------------------
# S7. Other shapes. Docs promise IPs, hosts, emails and house numbers hidden.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,value", [
    ("I live at 742 Oak Way", "742"),
    ("PO Box 1234", "1234"),
    ("office at 1 Infinite Loop", "Infinite Loop"),
    ("server 10[.]1[.]2[.]3", "10"),
    ("bob[at]example[.]com", "example"),
    ("Authorization: Basic orange", "orange"),
])
def test_leak_s7_other_shape(text, value):
    assert_gone(text, [value])


# ---------------------------------------------------------------------------
# S8. log_message tests "404" against the request line, not the status, so a
# session id or query with "404" in it prints the hide list to the server log.
# ---------------------------------------------------------------------------
def test_leak_s8_hide_terms_in_server_log(server_module, capsys):
    line = "GET /api/conversations/abc404/share?hide=orange HTTP/1.1"
    handler = server_module.CommandCenterHandler.__new__(server_module.CommandCenterHandler)
    handler.path = "/api/conversations/abc404/share?hide=orange"
    handler.client_address = ("127.0.0.1", 1)
    handler.requestline = line
    handler.log_message('"%s" %s %s', line, "200", "-")
    assert "orange" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The final check reads raw text with its own tokenizer, so it refuses these
# even if the masker never saw them.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw", [
    "secret: orange",
    "token: orange",
    "api key: orange",
    "pin: 4821",
    "pwd: orange",
    "pass: orange",
    "password orange",
    "password - orange",
    "**password**: orange",
    "password:\norange",
    "password: [orange window",
    "+1 (415) 555-2671",
    "abandon | ability | able | about | above | absent | absorb | abstract | absurd",
])
def test_final_check_refuses_raw_shape(raw):
    with pytest.raises(RedactionError):
        Redactor().verify([raw])


# ---------------------------------------------------------------------------
# Attacks that held up.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("payload", [
    "</title><script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "</script><script>alert(1)</script>",
    "<svg/onload=alert(1)>",
    "<style>*{display:none}</style>",
])
def test_holds_no_markup_injection(payload):
    events = [
        {"type": "user_text", "text": payload + " fix", "ts": TS},
        {"type": "assistant", "blocks": [{"kind": "text", "text": payload}]},
    ]
    replay = replay_of(events, title=payload)
    html = share_run.render_html(replay)
    svg = share_run.render_svg(replay)
    assert len(re.findall(r"<script\b", html)) == 1      # only the page's own
    for doc in (html, svg):
        assert "<img" not in doc and "<svg/" not in doc
        assert not re.search(r"<[a-z][^>]*\son\w+=", doc)


def test_holds_422_does_not_echo_the_value():
    seed = "\n".join(f"#{i} {w}" for i, w in enumerate(SEED, 1))
    payload, status = share_run.export_conversation(
        "x",
        parse=lambda _: {"events": [{"type": "user_text", "text": seed + "\nsk-ant-test-XXXX"}]},
        transcript_path="",
        engine="claude",
    )
    if status == 422:
        text = json.dumps(payload)
        assert not any(w in text for w in SEED)
        assert set(payload) == {"ok", "error", "detail"}


@pytest.mark.parametrize("text", [
    "４１１１ １１１１ １１１１ １１１１",
    "٤١١١ ١١١١ ١١١١ ١١١١",
    "4111 1111 1111 1111",
    "4111 1111 1111 1111",
    "4​1​1​1 1111 1111 1111",
    "4111 * 1111 * 1111 * 1111",
    "4111 | 1111 | 1111 | 1111",
])
def test_holds_card_unicode_forms(text):
    out = exported(text)
    assert out is None or "1111" not in digits_of(out) and "١" not in out and "１" not in out


def test_kilo_session_cwd_reads_the_directory(monkeypatch):
    from ccc_server import kilo
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE session (id TEXT, directory TEXT)")
    con.execute("INSERT INTO session VALUES ('s1', '/tmp/x/orange-window')")
    monkeypatch.setattr(kilo, "_kilo_connect", lambda: con)
    assert kilo._kilo_session_cwd("s1") == "/tmp/x/orange-window"


def test_too_long_hide_file_refuses_the_export(monkeypatch, tmp_path):
    monkeypatch.undo()   # this one reads the real function, on a temp file
    hide_file = tmp_path / "share-hide.txt"
    hide_file.write_text("\n".join(f"w{i}" for i in range(share_run.MAX_LIST_ITEMS + 1)))
    monkeypatch.setattr(share_run, "_hide_file", lambda: hide_file)
    payload, status = share_run.export_conversation(
        "x", parse=lambda _: {"events": [{"type": "user_text", "text": "hello", "ts": TS}]},
        transcript_path="", engine="claude",
    )
    assert status == 422 and not payload["ok"]


def test_final_check_does_not_mistake_prose_for_a_seed():
    # A run the masker may trim, broken by one ordinary word, is not a refusal.
    Redactor().verify(["ability able about above absent the absorb abstract absurd"])


def test_status_label_is_masked_not_refused():
    redactor = Redactor()
    out = redactor.text("Pass: suite one 12 of 13, more detail")
    assert "detail" not in out
    redactor.verify([out])


def test_final_check_accepts_a_file_extension_with_a_digit():
    redactor = Redactor()
    out = redactor.text("saved `clip.mp4` to the folder")
    assert "clip.mp4" in out
    redactor.verify([out])


def test_final_check_does_not_join_digits_across_a_placeholder():
    Redactor().verify(["**12 [name-1] 345 [word-1] 678 [word-2] 90**"])
