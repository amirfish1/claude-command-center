"""A send to a session whose cwd is unusable must fail loudly, not queue.

Incident (2026-09-20): a scheduled run anchored at cwd "/" got a composer
reply. Resume rejected the root cwd (invalid_cwd), the router queued the text
and told the client "Queued - will send when the folder is restored", and the
terminal-queue watcher dropped it as dead_session ~4s later. The message was
lost while the UI still said "Queued".
"""
import importlib

from ccc_server import watchtower_msg

server = importlib.import_module("server")


def test_invalid_cwd_returns_failure_and_never_queues(monkeypatch):
    queued = []
    monkeypatch.setattr(
        watchtower_msg._core, "_queue_terminal_input",
        lambda *a, **k: queued.append(a) or {"ok": True, "queued": True},
    )
    result = watchtower_msg._maybe_queue_on_invalid_cwd(
        "sid", "hello", {"live": False},
        {"ok": False, "code": "invalid_cwd", "path": "/", "error": "root"},
    )
    assert queued == []
    assert result["ok"] is False
    assert result["queued"] is False
    assert result["code"] == "invalid_cwd"
    assert result["cwd_missing"] is True
    assert result["missing_path"] == "/"


def test_other_results_pass_through_untouched():
    ok = {"ok": True, "via": "resume"}
    assert watchtower_msg._maybe_queue_on_invalid_cwd("s", "t", {}, ok) is ok
    other = {"ok": False, "code": "boom"}
    assert watchtower_msg._maybe_queue_on_invalid_cwd("s", "t", {}, other) is other
