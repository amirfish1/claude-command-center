"""Hard redaction for shared runs: what must never survive, what should."""
import time

import pytest

from ccc_server import share_redact
from ccc_server.share_redact import NAME_WORDS, RedactionError, Redactor


def redact(text, **kwargs):
    return Redactor(**kwargs).text(text)


FAKE = "X" * 36

PATHS = [
    "/home/dana/work/billing/app.py",
    "/Users/dana/Library/Application Support/thing",
    "~/secret-project/notes.md",
    "./scripts/deploy.sh",
    "../other/thing.txt",
    "src/payments/refunds/api.py",
    r"C:\Users\dana\Documents\plan.docx",
    r"\\fileserver\share\plan.docx",
    "/etc/nginx/sites-enabled/default",
    "/mnt/data/exports",
]

SECRETS = [
    "sk-ant-test-XXXX" + FAKE,
    "sk-proj-" + FAKE,
    "sk_live_" + FAKE,
    "rk_test_" + FAKE,
    "ghp_" + FAKE,
    "gho_" + FAKE,
    "github_pat_" + FAKE,
    "glpat-" + FAKE,
    "xoxb-1234-5678-" + FAKE,
    "AKIA" + "X" * 16,
    "AIza" + "X" * 35,
    "ya29." + FAKE,
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJl",
    "whsec_" + FAKE,
    "npm_" + FAKE,
    "pypi-" + FAKE,
    "hf_" + FAKE,
    "SG." + FAKE,
    "AC" + "0" * 32,
    "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6",
    "k3j9x2m1",
]

NAMES = [
    "Priya", "Nguyen", "Okafor", "Dmitri", "Chukwuemeka", "Siobhan", "Zhang",
    "priya", "okafor", "PRIYA", "O'Brien", "Mary-Jane", "Ashley", "Tanaka",
]


@pytest.mark.parametrize("value", PATHS)
def test_paths_are_masked(value):
    out = redact(f"see {value} for details")
    assert value not in out
    assert "dana" not in out
    assert "[path]" in out


def test_path_holding_an_identity_word_stays_one_placeholder():
    out = redact("edit /home/dana/app.py now", identity=["dana"])
    assert out == "edit [path] now"


@pytest.mark.parametrize("value", SECRETS)
def test_secret_tokens_are_masked(value):
    out = redact(f"the value {value} was printed")
    assert value not in out
    assert value[-6:] not in out


@pytest.mark.parametrize("text,leak", [
    ("password=dragon", "dragon"),
    ("PASSWORD: dragon", "dragon"),
    ('"api_key": "correct horse battery"', "battery"),
    ("DB_PASSWORD='letmein'", "letmein"),
    ("export GITHUB_TOKEN=abcdef", "abcdef"),
    ("the password is dragon", "dragon"),
    ("Authorization: Bearer abcdefghijkl", "abcdefghijkl"),
    ("session_id=rabbit", "rabbit"),
])
def test_secret_values_with_ordinary_words_are_masked(text, leak):
    assert leak not in redact(text)


def test_private_key_block_is_masked():
    block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\ndefghi\n-----END RSA PRIVATE KEY-----"
    out = redact("key:\n" + block + "\nend")
    assert "MIIE" not in out and "defghi" not in out and "PRIVATE" not in out


@pytest.mark.parametrize("name", NAMES)
def test_names_are_masked_without_being_listed_anywhere(name):
    out = redact(f"please ask {name} about the invoice")
    assert name.lower() not in out.lower()
    assert "please ask" in out and "about the invoice" in out


def test_capitalized_name_that_is_also_a_word_is_masked():
    assert "Mark" not in redact("Then Mark reviewed the change")
    assert "mark the task as done" in redact("mark the task as done")


def test_word_next_to_a_masked_name_is_masked():
    out = redact("I spoke with Priya Fish today and Stone Nguyen too")
    assert "Fish" not in out and "Stone" not in out


@pytest.mark.parametrize("text,leak", [
    ("שלום דנה", "דנה"),
    ("你好世界", "世界"),
    ("José Müller", "Jos"),
    ("Dаna", "Dа"),  # Cyrillic а
    ("Da\u200bna Okafor", "Okafor"),
    ("Ｏｋａｆｏｒ", "Okafor"),
])
def test_unicode_tricks_do_not_slip_through(text, leak):
    assert leak not in redact(text)


def test_zero_width_cannot_split_a_secret_or_identity():
    assert "ghp_" not in redact("ghp\u200b_" + FAKE)
    assert "dana" not in redact("da\u200bna", identity=["Dana"]).lower()


@pytest.mark.parametrize("text,leak", [
    ("mail dana@example.com today", "dana"),
    ("see https://internal.example.org/board/42?user=dana", "internal"),
    ("clone git@github.com:dana/billing.git", "billing"),
    ("host db-3.corp.internal is down", "corp"),
    ("server 10.20.30.40 replied", "10.20"),
    ("mac aa:bb:cc:dd:ee:ff seen", "aa:bb"),
    ("ipv6 fe80::1ff:fe23:4567:890a seen", "fe80"),
    ("call +1 (415) 555-0100", "555"),
    ("card 4242 4242 4242 4242", "4242"),
    ("ssn 078-05-1120", "078"),
    ("id 3f2504e0-4f89-41d3-9a0c-0305e82c3301", "3f2504e0"),
    ("commit 8f3a1c9d was bad", "8f3a1c9d"),
    ("ticket ACME-1234 again", "ACME"),
    ("reply to @dana_codes", "dana_codes"),
    ("on 2026-11-05T22:10:00Z it broke", "2026"),
    ("the invoice was $14,900", "14,900"),
])
def test_contact_and_infrastructure_details_are_masked(text, leak):
    assert leak not in redact(text)


def test_identity_tokens_are_masked_even_when_ordinary_words():
    out = redact("Stone fixed it; the stone was rolling", identity=["Rosa Stone"])
    assert "stone" not in out.lower()


def test_project_phrase_is_masked_without_banning_its_words():
    out = redact(
        "command-center failed, so run the command again",
        phrases=["command-center"],
    )
    assert "command-center" not in out
    assert "run the command again" in out


def test_generic_account_names_do_not_garble_prose():
    out = redact("the user and the admin went home", identity=["user", "admin", "home"])
    assert out == "the user and the admin went home"


def test_hide_list_masks_ordinary_words():
    out = redact("We migrated Target to the new billing flow", hide=["Target"])
    assert "Target" not in out


def test_keep_lifts_word_masks_but_never_detectors():
    out = redact("Zorblax shipped; token=Zorblax", keep=["Zorblax"])
    assert out.startswith("Zorblax shipped")
    assert "token=[secret]" in out
    assert "ghp_" not in redact("ghp_" + FAKE, keep=["ghp_" + FAKE])


def test_a_masked_word_keeps_one_placeholder_per_export():
    redactor = Redactor()
    first = redactor.text("Zorblax broke")
    second = redactor.text("then zorblax recovered")
    assert first.split()[0] == second.split()[1]


@pytest.mark.parametrize("text", [
    "Fix the login bug and add a regression test",
    "I'll restart the server now, then run the tests.",
    "Refactor parseConfig into loadUserSettings; 3 files changed, 12% faster",
    "Added a share button and/or a CI/CD step in v1.2.3",
    "The README.md and server.py are updated",
    "## What Changed\n- Added **tests**",
])
def test_ordinary_engineering_prose_survives_unchanged(text):
    assert redact(text) == text


def test_code_blocks_and_injected_context_are_dropped():
    out = redact(
        "<system-reminder>private Dana</system-reminder>Before\n"
        "```python\nprint('Dana')\nimport billing\n```\nAfter<!-- hidden -->"
    )
    assert "Dana" not in out and "billing" not in out and "hidden" not in out
    assert "[code, 2 lines]" in out
    assert "Before" in out and "After" in out


def test_unterminated_fence_hides_the_rest():
    assert "Okafor" not in redact("intro\n```\nDana Okafor\nand more")


def test_truncation_never_splits_a_placeholder():
    out = Redactor().text("hello " * 3 + "Zorblax " * 40, limit=40)
    assert out.count("[") == out.count("]")


def test_a_missing_word_list_fails_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(share_redact, "_WORDLIST", tmp_path / "gone.txt")
    share_redact._words.cache_clear()
    try:
        assert "ordinary" not in redact("an ordinary sentence")
    finally:
        monkeypatch.undo()
        share_redact._words.cache_clear()


def test_word_list_is_vocabulary_not_people():
    words = share_redact._words()
    assert len(words) > 8000
    for name in NAME_WORDS:
        assert not Redactor()._word_ok(name.capitalize()), name
        assert not Redactor()._word_ok(name), name


@pytest.mark.parametrize("text,kwargs", [
    ("/home/dana/x", {}),
    ("dana@example.com", {}),
    ("https://example.com/a", {}),
    ("10.1.2.3", {}),
    ("3f2504e0-4f89-41d3-9a0c-0305e82c3301", {}),
    ("ghp_" + FAKE, {}),
    ("sk-ant-test-XXXX" + FAKE, {}),
    ("a1b2c3d4e5f6a7b8c9d0e1f2", {}),
    ("-----BEGIN PRIVATE KEY-----", {}),
    ("Dana reviewed it", {"identity": ["Dana"]}),
    ("my command-center repo", {"phrases": ["command-center"]}),
])
def test_verify_refuses_anything_forbidden_without_echoing_it(text, kwargs):
    with pytest.raises(RedactionError) as caught:
        Redactor(**kwargs).verify([text])
    assert text not in str(caught.value)


def test_verify_accepts_redacted_output():
    redactor = Redactor(identity=["Dana Okafor"], phrases=["acme-billing"])
    corpus = [f"see {p}" for p in PATHS] + [f"value {s}" for s in SECRETS] + [
        "Dana Okafor at acme-billing, dana@example.com, https://example.com/x, 10.1.2.3",
    ]
    redactor.verify([redactor.text(text) for text in corpus])


@pytest.mark.parametrize("value", [
    "a" * 200_000, "/x" * 100_000, " @" * 100_000, "a." * 100_000, "a-" * 100_000,
    "a:" * 100_000, "password " * 20_000, "<" * 200_000, "```\n" * 50_000,
    "Foo " * 50_000, "1.2.3." * 30_000, "1 " * 100_000, "=" * 200_000,
])
def test_hostile_input_stays_fast(value):
    started = time.monotonic()
    Redactor(identity=["Dana Okafor"]).text(value)
    assert time.monotonic() - started < 2.0


def test_bare_scheme_in_prose_is_masked_not_refused():
    redactor = Redactor()
    masked = redactor.text("Links that start with `http://` are rejected.")
    assert "://" not in masked
    redactor.verify([masked])
