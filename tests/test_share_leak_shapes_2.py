"""More leak shapes from a second independent review of Share this run.

All data is invented. Nothing here reads a real transcript or contacts a service.
A RedactionError counts as safe, except in test_not_refused_*, which prove that an
ordinary run is not refused outright.
"""
import json
import re
import time

import pytest

from ccc_server import share_redact, share_run
from ccc_server.share_redact import RedactionError, Redactor

TS = "2026-01-01T00:00:00Z"
PLACEHOLDER = re.compile(r"\[[a-z]+(?:-\d+)?(?:, \d+ lines?)?\]")
SEED = "abandon ability able about above absent absorb abstract absurd abuse access accident".split()


@pytest.fixture(autouse=True)
def no_real_hide_list(monkeypatch):
    monkeypatch.setattr(share_run, "_user_hide_list", lambda: [])


@pytest.fixture(scope="module")
def server_module():
    import server
    return server


def replay_of(events, engine="claude", **kw):
    # A session with no typed prompt is refused, which would hide the row under test.
    typed = {"type": "user_text", "entrypoint": "cli", "text": "go", "ts": TS}
    replay, _ = share_run.build_replay([*events, typed], engine=engine, identity=kw.pop("identity", {}), **kw)
    return replay


def page_text(replay):
    return " || ".join([replay.get("title") or ""] + [s.get("text") or "" for s in replay["steps"]])


def exported(text, **kw):
    """Full path: masks, then verify(). None means the export was refused."""
    try:
        return page_text(replay_of([{"type": "user_text", "entrypoint": "cli", "text": text, "ts": TS}], **kw))
    except RedactionError:
        return None


def assert_gone(text, values, **kw):
    out = exported(text, **kw)
    if out is None:
        return
    left = [v for v in values if re.search(rf"(?<![\w-]){re.escape(v)}(?![\w-])", PLACEHOLDER.sub(" ", out))]
    assert not left, f"survived {left} in {out!r}"


def digits_of(out):
    return re.sub(r"\D", "", PLACEHOLDER.sub(" ", out))


def assert_digits_gone(text, digits):
    out = exported(text)
    if out is None:
        return
    assert digits not in digits_of(out), out


# ---------------------------------------------------------------------------
# REGRESSION. The final check refuses ordinary prose, so the whole export
# dies. _VERIFY_CRED_RE allows any 1-3 non-alphanumerics between "pass" and
# "code/word/key"; the masker's _CRED_WORD_RE allows only space . _ -. So the
# masker leaves "pass, code is clean" alone and verify() calls it a secret.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "All tests pass, code is clean.",
    "tests pass, code pushed",
    "Lint and tests pass (code coverage is 90%)",
    "the build will pass, key risks are low",
    "if they pass, word it differently",
    "tests pass; code review next",
])
def test_not_refused_m1_prose_with_pass_then_code(text):
    assert exported(text) is not None, "export refused for an ordinary sentence"


def test_not_refused_m1_one_agent_reply_kills_the_export():
    events = [
        {"type": "user_text", "entrypoint": "cli", "text": "fix the flaky test", "ts": TS},
        {"type": "assistant", "ts": TS, "model": "claude-opus-5-5",
         "blocks": [{"kind": "text", "text": "Done. All tests pass, code is pushed."}]},
    ]
    replay_of(events)


# ---------------------------------------------------------------------------
# REGRESSION. verify() now refuses any number of 5+ digits, and the model
# name is verified. _public_model keeps dated ids, so every session whose main
# model has a date suffix is refused. Parent e6fb9009 exported the claude ids. The
# gpt id was already refused there (a 10-digit run), so that one case is not new.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("model", [
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-20250514",
    "claude-haiku-4-5-20251001",
    "claude-3-5-sonnet-20241022",
    "gpt-4.1-2025-04-14",
])
def test_not_refused_m2_dated_model_id(model):
    events = [
        {"type": "user_text", "entrypoint": "cli", "text": "go", "ts": TS},
        {"type": "assistant", "ts": TS, "model": model, "blocks": [{"kind": "text", "text": "done"}]},
    ]
    replay_of(events)


# ---------------------------------------------------------------------------
# _label_lines hides exactly one following non-blank line, and any line
# counts, even a table rule. So the value in the row below survives, and only
# the first row of a table, CSV or list is covered. verify() checks the same
# single next line, which by then is a placeholder.
# ---------------------------------------------------------------------------
V = ["orange", "window"]


@pytest.mark.parametrize("text", [
    "| user | password |\n|---|---|\n| admin | orange window |",
    "| password | user |\n|---|---|\n| orange window | admin |",
    "| site | password | note |\n|---|---|---|\n| home | orange window | old |",
    "Password\n========\norange window",
    "Password\n--------\norange window",
    "password:\n---\norange window",
    "user,password\nalice,purple lemon\nbob,orange window",
    "passwords:\n  - purple lemon\n  - orange window",
    "Passwords:\nhome: purple lemon\noffice: orange window",
    "credentials:\nadmin\norange window",
])
def test_leak_m3_value_below_a_label(text):
    assert_gone(text, V)


@pytest.mark.parametrize("text", [
    "| pin |\n|---|\n| 482 |",
    "| user | pin |\n|---|---|\n| admin | 482 |",
])
def test_leak_m3_pin_in_table(text):
    assert_digits_gone(text, "482")


# ---------------------------------------------------------------------------
# Seed phrase with a letter in each gap. _SEED_GAP_RE rejects any letter,
# so a) b) c), 1st 2nd or w1 w2 break the run after every word. _verify_seed
# resets on every non-list token, so it is blind in the same place.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "\n".join(f"{chr(97 + i)}) {w}" for i, w in enumerate(SEED)),
    " ".join(f"{chr(65 + i)}. {w}" for i, w in enumerate(SEED)),
    " ".join(f"{i}{'st' if i == 1 else 'nd' if i == 2 else 'rd' if i == 3 else 'th'} {w}"
             for i, w in enumerate(SEED, 1)),
    " ".join(f"w{i}: {w}" for i, w in enumerate(SEED, 1)),
])
def test_leak_m4_seed_with_letters_in_the_gaps(text):
    out = exported(text)
    if out is None:
        return
    readable = [w for w in SEED if re.search(rf"\b{w}\b", out)]
    assert len(readable) < 3, f"{len(readable)} of 12 seed words readable: {out!r}"


# ---------------------------------------------------------------------------
# Digit secrets. "Every digit after a word like pin or cvv" holds only for
# the exact words: \b rejects cvv2, pincode and pin_code, and "code" is not on
# the list at all, so a code split in two escapes _PIN_RE's \d{3,8}. _PIN_NOUN's
# "to" exception keeps "the pin to ..." out of the every-digit rule, and
# _VERIFY_PIN_RE shares it, so the final check is blind to the same text. These
# inputs behave the same on parent e6fb9009: the new rule missed them, it did not
# cause them.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,digits", [
    ("cvv2: 482", "482"),
    ("cvc2 482", "482"),
    ("cvv\n482", "482"),
    ("**CVV**\n482", "482"),
    ("security code\n482", "482"),
    ("482 is my cvv", "482"),
    ("482 is the door code", "482"),
    ("pincode 482", "482"),
    ("pincode 48 21", "4821"),
    ("pin_code 48 21", "4821"),
    ("door code 48 21", "4821"),
    ("gate code: 48-21", "4821"),
    ("the safe code is 1 2 3 4", "1234"),
    ("lock code 48 21", "4821"),
    ("verification code 123-456", "123456"),
    ("backup code 123-456", "123456"),
    ("2fa 123-456", "123456"),
    ("verification code 123 456", "456"),
    ("your otp\n123 456", "123456"),
    ("the pin to unlock my old work phone is 48 21", "4821"),
    ("the pin to get into my old work phone was 4 8 2 1", "4821"),
    ("pin to my safe 48 21", "4821"),
])
def test_leak_m5_digit_secret(text, digits):
    assert_digits_gone(text, digits)


# ---------------------------------------------------------------------------
# _is_agent_reply checks subtype, _is_typed_prompt does not. Hermes gives
# messages other people sent on WhatsApp the user_text row shape with a subtype,
# so they export as "Prompt". The owner's own bridge rows are dropped; the other
# side's are kept. Plain words survive redaction, which is why rows must not.
# ---------------------------------------------------------------------------
def test_leak_m6_other_persons_whatsapp_message(server_module):
    from ccc_server import hermes
    line = json.dumps({"event": "upsert", "chatId": "c1@g.us", "fromMe": False, "senderId": "x",
                       "time": 1767225600, "body": "my lawyer called about the lawsuit"})
    event = hermes._hermes_parse_bridge_line(line, 1)
    with pytest.raises(share_run.ChatBridgeSession):
        replay_of([event], engine="hermes")


def test_leak_m6_pending_request_from_another_person():
    # Shape copied from hermes._parse_hermes_pending_conversation.
    event = {"ts": TS, "type": "user_text", "subtype": "hermes_pending_request",
             "source_platform": "whatsapp", "sender_name": "x",
             "text": "my lawyer called about the lawsuit"}
    with pytest.raises(share_run.ChatBridgeSession):
        replay_of([event], engine="hermes")


# ---------------------------------------------------------------------------
# Machine-written prompts. _MACHINE_ROW_PREFIXES lists 7 forms, but
# runaway.py already knows the rest (_AUTO_PREFIXES, _ANSWER_RE, turnOrigin), and
# docs/sharing.md lists hook output, other sessions and IDE selections as left out.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "Stop hook feedback:\norange lawsuit output",
    "[WATCHTOWER] ticket about the orange lawsuit",
    "Another Claude session sent this: orange lawsuit",
    "[Cross-session note] orange lawsuit",
    "Announced from: other\norange lawsuit",
    "A human answered your blocked question on ticket WT-1: orange lawsuit",
])
def test_leak_m7_claude_injected_prompt(server_module, text):
    row = {"type": "user", "timestamp": TS, "message": {"role": "user", "content": text}}
    event = server_module._parse_conversation_event(row, 0)
    assert "lawsuit" not in page_text(replay_of([event]))


@pytest.mark.parametrize("field", [
    {"turnOrigin": "task_notification"},
    {"turnOrigin": "system"},
    {"turnOrigin": "peer"},
])
def test_leak_m7_claude_turn_origin_not_a_person(server_module, field):
    row = {"type": "user", "timestamp": TS, **field,
           "message": {"role": "user", "content": "note about the orange lawsuit"}}
    event = server_module._parse_conversation_event(row, 0)
    assert "lawsuit" not in page_text(replay_of([event]))


def test_leak_m7_codex_ide_context():
    from ccc_server import codex_parse
    message = ("# Context from my IDE setup:\n\n## Open tabs:\n- lawsuit-notes.md\n\n"
               "## My request for Codex:\nfix it")
    row = {"type": "event_msg", "timestamp": TS, "payload": {"type": "user_message", "message": message}}
    event = codex_parse._parse_codex_event(row, 0)
    assert "lawsuit" not in page_text(replay_of([event], engine="codex"))


# ---------------------------------------------------------------------------
# Assistant rows that are not the agent's reply, through the real parsers.
# Copilot: any event type holding "assistant" or "message" becomes a text block.
# Devin Fusion: actor="sidekick" is not checked; only the report row was covered.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind,word", [
    ("assistant.reasoning", "thinking"),
    ("system.message", "instructions"),
    ("subagent.message", "subagent"),
])
def test_leak_m8_copilot_non_reply_event(server_module, monkeypatch, tmp_path, kind, word):
    from ccc_server import copilot_cli
    path = tmp_path / "events.jsonl"
    rows = [{"type": "user.message", "timestamp": TS, "data": {"content": "fix it"}},
            {"type": kind, "timestamp": TS, "data": {"content": f"private {word} about the lawsuit"}},
            {"type": "assistant.message", "timestamp": TS, "data": {"content": "done"}}]
    path.write_text("\n".join(json.dumps(r) for r in rows))
    monkeypatch.setattr(copilot_cli, "_copilot_events_path", lambda _sid: path)
    events = copilot_cli._parse_copilot_conversation("s")["events"]
    assert "lawsuit" not in page_text(replay_of(events, engine="copilot"))


def test_leak_m8_devin_sidekick_turn(server_module):
    from ccc_server import devin
    dims = [{"uid": "model", "kind": {"Metric": {"value": "SWE 2 Medium"}}}]
    message = json.dumps({"role": "assistant", "content": "sidekick notes about the lawsuit",
                          "metadata": {"response_dimensions": dims}})
    event = devin._devin_cli_parse_message_row(message, 1767225600, set(), "swe2medium")
    assert event["actor"] == "sidekick"
    assert "lawsuit" not in page_text(replay_of([event], engine="devin"))


# ---------------------------------------------------------------------------
# A company name made of two allowed words. _piece_ok splits camelCase and
# passes each half, so the compound is never checked as one word. The docs say a
# client name CCC has never seen is hidden anyway. These are not dictionary words.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,value", [
    ("BrightPath Capital signed", "BrightPath"),
    ("NorthStar renewed", "NorthStar"),
])
def test_leak_m9_compound_company_name(text, value):
    assert_gone(text, [value])


# ---------------------------------------------------------------------------
# Value before the label. _VALUE_IS_LABEL_RE and _VALUE_AS_LABEL_RE take one
# token, so a two-part value keeps its first part or does not match at all.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,values", [
    ("use orange window as the password", V),
    ("set orange window as my new wifi password", V),
    ("orange window is my password", V),
])
def test_leak_s1_value_before_label(text, values):
    assert_gone(text, values)


@pytest.mark.parametrize("text,left", [("48 21 is my pin", "48"), ("1 2 3 4 is my pin", "123")])
def test_leak_s1_digits_before_label(text, left):
    assert_digits_gone(text, left)


# ---------------------------------------------------------------------------
# Label vocabulary. Same plain-word value, with a label form the location
# rule does not know.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "secret\norange window",
    "token\norange window",
    "pw\norange window",
    "pw orange window",
    "pwd orange window",
    "secret orange window",
    "creds orange window",
    "pswd: orange window",
    "psw: orange window",
    "p4ssword: orange window",
    "wifi key: orange window",
    "key: orange window",
    "zip -P orange window.zip",
    "redis-cli -a orange",
    "requirepass orange",
])
def test_leak_s2_label_form(text):
    assert_gone(text, ["orange", "window"][: 1 if "window.zip" in text else 2])


# ---------------------------------------------------------------------------
# Other-language labels get none of the location rule. The colon is taken
# as the value, and a comma ends the clause.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "contraseña:\norange window",
    "mot de passe :\norange window",
    "密码\norange window",
    "contraseña: orange, window",
    "Passwort ist: orange, window",
])
def test_leak_s3_other_language_label(text):
    assert_gone(text, V)


# ---------------------------------------------------------------------------
# docs/sharing.md: "any word joined to digits" is hidden and the final check
# refuses it. Joined by . : - or _ it is not (_SPLIT_DIGITS_RE wants 3+ digits
# and only - or _).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["Welcome.123", "Welcome:123", "Welcome-12", "Welcome_99"])
def test_leak_s4_word_joined_to_digits(text):
    assert_gone(text, [text])


# ---------------------------------------------------------------------------
# docs/sharing.md: "a bare number of four or more digits becomes [number]".
# A thousands comma splits it into chunks of three or fewer, and two groups stay
# under the three-group rule.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,value", [
    ("her salary is 185,000", "185,000"),
    ("I owe 45,000 on the loan", "45,000"),
    ("my ssn ends in 6,789", "6,789"),
])
def test_leak_s5_comma_number(text, value):
    assert_gone(text, [value])


# ---------------------------------------------------------------------------
# docs/sharing.md: "Dates with a year and a day after a birth word are hidden".
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,value", [
    ("dob 3-14-85", "3-14-85"),
    ("my birthday is 3-14", "3-14"),
    ("birthday 03-14", "03-14"),
    ("birthday: 14.03.", "14.03"),
    ("born on the 14th of March", "14th"),
    ("March 14 '85", "85"),
    ("14 Mar 85", "85"),
])
def test_leak_s6_birth_date(text, value):
    assert_gone(text, [value])


# ---------------------------------------------------------------------------
# Also hide. _denied only strips a plain suffix from the listed word.
# Hide-beats-keep compares casefolded forms, so an accent difference slips past.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,hide,value", [
    ("our parties", "party", "parties"),
    ("the planning", "plan", "planning"),
    ("it was planned", "plan", "planned"),
    ("one lawsuit", "lawsuits", "lawsuit"),
])
def test_leak_s7_hide_inflection(text, hide, value):
    assert_gone(text, [value], hide=[hide])


@pytest.mark.parametrize("hide,keep,text", [
    ("resume", "Résumé", "Résumé"),
    ("Résumé", "resumé", "resumé"),
])
def test_leak_s7_keep_beats_hide_across_accents(hide, keep, text):
    assert_gone(text, [text], hide=[hide], keep=[keep])


# ---------------------------------------------------------------------------
# docs/sharing.md: "So are your repo and folder names." Outside the home
# folder only the last folder is taken, and transcript_cwds keeps the first 8 in
# sort order, dropping the rest with no error.
# ---------------------------------------------------------------------------
def test_leak_s8_parent_folder_outside_home():
    identity = share_run.machine_identity(["/srv/clients/summit-harbor/app"])
    assert "summit-harbor" in identity["projects"]


def test_leak_s8_ninth_folder_is_dropped(tmp_path):
    path = tmp_path / "t.jsonl"
    names = "a b c d e f g h zebra-harbor".split()
    path.write_text("\n".join(json.dumps({"cwd": f"/srv/w/{n}"}) for n in names))
    assert "/srv/w/zebra-harbor" in share_run.transcript_cwds(str(path))


# ---------------------------------------------------------------------------
# _PHONE_RE runs first and stops at 24 characters. What is left of a card
# typed digit by digit is under the seven-digit group rule, so the last four show.
# ---------------------------------------------------------------------------
def test_leak_s9_digit_by_digit_card_keeps_last_four():
    assert_digits_gone("1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6", "3456")


# ---------------------------------------------------------------------------
# Personal names on the allow-list that are not ordinary words.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,value", [
    ("ask Sinclair about it", "Sinclair"),
    ("Tal said no", "Tal"),
    ("Bao said no", "Bao"),
])
def test_leak_s10_name_on_the_allow_list(text, value):
    assert_gone(text, [value])


# ---------------------------------------------------------------------------
# Attacks that held up.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("label", [
    "password", "PASSWORD", "passwd", "passphrase", "passcode", "passkey", "pass word",
    "api key", "access key", "private key", "authorization", "secret word", "secret:",
    "token:", "credentials:", "creds:", "otp:", "cvv:", "cvc:", "pwd:", "pw:", "pass:",
    "security code:", "pin:", "pin code:", "user bob pass",
])
def test_holds_cred_hints_never_skips_a_label(label):
    # Every string the _label_lines patterns match holds a prefilter hint.
    assert any(h in label.casefold() for h in share_redact._CRED_HINTS)
    assert_gone(f"{label} orange window", ["orange"])


@pytest.mark.parametrize("name,text", [
    ("digits and gaps", ("1" + "." * 39) * 500),
    ("digit runs", "123456 " * 2900),
    ("separators", "-" * 20000),
    ("hold-free colons", ":" * 20000),
    ("labels", "password\n" * 2200),
    ("pin words", "pin a " * 3300),
])
def test_holds_hostile_input_stays_fast(name, text):
    started = time.monotonic()
    Redactor().text(text)
    assert time.monotonic() - started < 3, name


def test_holds_long_hide_list_stays_fast():
    words = [f"zq{i}x" for i in range(1000)]
    started = time.monotonic()
    Redactor(hide=words).text("the quick brown fox " * 100)
    assert time.monotonic() - started < 3


@pytest.mark.parametrize("text,gone", [
    ("pin ٤٨٢١", "٤٨٢١"),
    ("٤١١١ ١١١١ ١١١١ ١١١١", "١١١١"),
])
def test_holds_non_ascii_digits(text, gone):
    out = exported(text)
    assert out is None or gone not in out


@pytest.mark.parametrize("text,hide", [
    ("12 and sk-abcdef123456", "12"),
    ("[secret] 0 1 2", "secret"),
    ("/etc/x [path]", "path"),
])
def test_holds_hold_markers_not_touched_by_a_hide_word(text, hide):
    out = exported(text, hide=[hide])
    assert out is None or "\ue000" not in out


def test_holds_keep_does_not_undo_a_label():
    assert_gone("password: orange", ["orange"], keep=["orange"])


def test_digit_word_with_a_common_word_stays_readable():
    assert "top-10" in Redactor().text("the top-10 list")


def test_type_names_stay_readable():
    assert "ConfigParser" in Redactor().text("use ConfigParser here")
