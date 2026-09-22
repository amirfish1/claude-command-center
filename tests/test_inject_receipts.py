"""CCC-28: durable delivery receipts for queued injects.

The failure being guarded: a composer inject gets accepted into CCC's
terminal queue (`queued=True`) and then the queue's retry loop silently
re-parks or fails it forever with no log line and no way to prove it never
landed short of manually diffing last-interactions.json against the
transcript. These tests pin the receipt lifecycle (open on queue-accept,
close on proven landing, stay outstanding otherwise) and the paired
watcher log lines that turned two previously-silent retry branches audible.
"""
import importlib

from ccc_server import inject_receipts as rc

NOW = 1_000_000.0


def test_fresh_receipt_is_not_outstanding_until_stale(tmp_path):
    p = str(tmp_path / "r.json")
    rc.open_receipt("s1", "inj-1", "hello", now=NOW, path=p)
    assert rc.outstanding("s1", now=NOW + 1, path=p) is None
    assert rc.outstanding("s1", now=NOW + rc.STALE_AFTER_S + 1, path=p) is not None


def test_outstanding_reports_the_open_receipt(tmp_path):
    p = str(tmp_path / "r.json")
    rc.open_receipt("s1", "inj-1", "please finish the task", source="composer", now=NOW, path=p)
    got = rc.outstanding("s1", now=NOW + 100, path=p)
    assert got["inject_id"] == "inj-1"
    assert got["text_preview"] == "please finish the task"
    assert got["source"] == "composer"
    assert got["age_s"] == 100


def test_close_receipt_clears_it(tmp_path):
    p = str(tmp_path / "r.json")
    rc.open_receipt("s1", "inj-1", "hi", now=NOW, path=p)
    rc.close_receipt("s1", now=NOW + 5, path=p)
    assert rc.outstanding("s1", now=NOW + 1000, path=p) is None


def test_close_receipt_by_text_only_clears_a_matching_entry(tmp_path):
    """A late confirmation for stale text must not clear a newer, genuinely
    outstanding receipt for the same session."""
    p = str(tmp_path / "r.json")
    rc.open_receipt("s1", "inj-1", "first message", now=NOW, path=p)
    rc.open_receipt("s1", "inj-2", "second message", now=NOW + 1, path=p)
    rc.close_receipt("s1", text="first message", now=NOW + 2, path=p)
    got = rc.outstanding("s1", now=NOW + 1000, path=p)
    assert got is not None and got["inject_id"] == "inj-2"


def test_close_receipt_by_inject_id_only_clears_a_matching_entry(tmp_path):
    p = str(tmp_path / "r.json")
    rc.open_receipt("s1", "inj-1", "m", now=NOW, path=p)
    rc.close_receipt("s1", inject_id="not-the-one", now=NOW + 1, path=p)
    assert rc.outstanding("s1", now=NOW + 1000, path=p) is not None
    rc.close_receipt("s1", inject_id="inj-1", now=NOW + 2, path=p)
    assert rc.outstanding("s1", now=NOW + 1000, path=p) is None


def test_reopening_the_same_inject_id_does_not_reset_sent_ts(tmp_path):
    """A retried API call for the exact same message (same idempotency key)
    must not look "freshly sent" and hide a genuinely stuck message behind
    the staleness floor forever."""
    p = str(tmp_path / "r.json")
    rc.open_receipt("s1", "inj-1", "m", now=NOW, path=p)
    rc.open_receipt("s1", "inj-1", "m", now=NOW + 1000, path=p)
    got = rc.outstanding("s1", now=NOW + 1001, path=p)
    assert got["sent_ts"] == NOW


def test_a_new_inject_supersedes_a_stale_unclosed_one(tmp_path):
    """Only the terminal queue's current head is ever in flight for a given
    session, so a fresh open_receipt for the same sid replaces whatever was
    tracked before rather than accumulating stale entries forever."""
    p = str(tmp_path / "r.json")
    rc.open_receipt("s1", "inj-1", "old", now=NOW, path=p)
    rc.open_receipt("s1", "inj-2", "new", now=NOW + 10, path=p)
    got = rc.outstanding("s1", now=NOW + 1000, path=p)
    assert got["inject_id"] == "inj-2"


def test_sessions_are_isolated(tmp_path):
    p = str(tmp_path / "r.json")
    rc.open_receipt("s1", "inj-1", "m", now=NOW, path=p)
    assert rc.outstanding("s2", now=NOW + 1000, path=p) is None


def test_wiring_is_exported():
    server = importlib.import_module("server")
    assert hasattr(server, "_drop_dead_terminal_queue")
    assert hasattr(server, "_force_restart_session")


def test_inject_receipt_endpoint_is_registered():
    """Additive API field (CCC-27/CCC-28 ask): an agent can read whether a
    session has an unproven queued inject directly, instead of inferring
    "stuck" from last-interactions-vs-transcript timing."""
    import pathlib
    server_py = pathlib.Path(__file__).parent.parent.joinpath("server.py").read_text(encoding="utf-8")
    assert '"^/api/session/[a-zA-Z0-9_-]+/inject-receipt$"' in server_py
    assert "_inject_receipts.outstanding(sid)" in server_py


def test_open_receipt_is_wired_into_inject_input_handler():
    import pathlib
    server_py = pathlib.Path(__file__).parent.parent.joinpath("server.py").read_text(encoding="utf-8")
    assert "if result.get(\"ok\") and result.get(\"queued\"):" in server_py
    assert "_inject_receipts.open_receipt(" in server_py
