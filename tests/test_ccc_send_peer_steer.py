"""`ccc send` from an agent session steers by default; a human shell queues.

Peer coordination ("holding release until your notice") is useless when it
waits behind the receiver's whole turn, so session-to-session sends default
to steer. --queue opts out; a human running `ccc send` keeps the plain send.
"""

import argparse
import importlib.machinery
import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import pytest


def _load_ccc_cli():
    ccc_path = Path(__file__).resolve().parent.parent / "ccc"
    loader = importlib.machinery.SourceFileLoader("ccc_cli_peer_steer", str(ccc_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


ccc = _load_ccc_cli()


def _args(**overrides):
    base = dict(
        server="http://127.0.0.1:8099", target="receiver-sid", text=["status", "update"],
        steer=False, queue=False, sender=None, new_if_large_and_stale=False,
        large_threshold=150_000, stale_seconds=3600, dry_run=False, stop=False,
        model=None, effort=None, report_to=None, json=True,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _send(author, **overrides):
    import sender_envelope as attribution
    attr = attribution.make("receiver-sid", who=attribution.author(**author), basis="runtime")
    posted = {}

    def fake_post(base, path, payload, timeout=60):
        posted.update(payload)
        return 200, {"ok": True}

    with mock.patch.object(ccc, "_resolve_server", return_value="http://127.0.0.1:8099"), \
         mock.patch.object(ccc, "_resolve_target", side_effect=lambda base, t: (t, None)), \
         mock.patch.object(ccc, "_post_json", side_effect=fake_post), \
         mock.patch.object(attribution, "capture_cli", return_value=attr), \
         mock.patch.object(attribution, "journal", return_value=None), \
         redirect_stdout(io.StringIO()):
        assert ccc.cmd_send(_args(**overrides)) == 0
    return posted


AGENT = dict(kind="session", ident="sender-sid", name="sender", engine="codex")
HUMAN = dict(kind="unknown", ident="", name="", engine="")


def test_agent_session_send_steers_by_default():
    assert _send(AGENT)["mode"] == "steer"


def test_agent_session_can_opt_into_queue():
    assert _send(AGENT, queue=True)["mode"] == "queue"


def test_human_shell_send_keeps_plain_send():
    assert "mode" not in _send(HUMAN)


def test_sending_to_yourself_does_not_steer():
    assert "mode" not in _send(AGENT, target="sender-sid")


@pytest.mark.parametrize("author,expected", [
    ({"kind": "session"}, True),
    ({"kind": "service"}, False),
    ({"kind": "unknown"}, False),
])
def test_only_session_authors_count_as_agents(author, expected):
    assert ccc._sent_by_agent_session({"author": author}, "a", "b") is expected
