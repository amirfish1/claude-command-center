"""Slim mode for /api/queue/list (?slim=1).

The board polls the whole ticket list, and closed tickets dominate it with
prose only a ticket's detail view reads. Slim mode trims that prose on closed
rows and must leave everything the list rows compute from untouched: open
rows whole, history event/at/by, resolution.unresolved and its acks.

Synthetic rows only — no network, no private data.
"""
import importlib
import sys


def _load_server():
    sys.modules.pop("server", None)
    return importlib.import_module("server")


_LONG = "x" * 5000


def _closed_row():
    return {
        "ref": "DEMO-1", "project": "DEMO", "status": "closed",
        "title": "synthetic closed ticket", "text": _LONG, "note": _LONG,
        "_github_body": _LONG, "closed_at": "2026-07-26T15:00:00Z",
        "resolution": {
            "summary": _LONG, "unresolved": ["still open thing"],
            "unresolved_ack": {"0": {"by": "dashboard"}}, "commit": "abc1234",
        },
        "history": [
            {"event": "claim", "at": "2026-07-26T14:00:00Z",
             "by": {"worker": "w1", "session_id": "s1"}},
            {"event": "close", "at": "2026-07-26T15:00:00Z", "by": "w1",
             "text": _LONG, "resolution": {"summary": _LONG}},
        ],
    }


def _open_row():
    return {
        "ref": "DEMO-2", "project": "DEMO", "status": "open",
        "text": _LONG, "_github_body": _LONG,
        "history": [{"event": "progress", "text": _LONG}],
    }


def test_closed_rows_lose_prose_but_keep_list_fields():
    server = _load_server()
    src = _closed_row()
    slim = server._ux_fixes_slim_items([src])[0]

    assert slim["_slim"] is True
    assert "_github_body" not in slim
    assert len(slim["text"]) <= server._SLIM_TEXT_MAX + 1
    assert len(slim["note"]) <= server._SLIM_TEXT_MAX + 1
    assert len(slim["resolution"]["summary"]) <= server._SLIM_TEXT_MAX + 1
    # Row markers and session crediting read these.
    assert slim["resolution"]["unresolved"] == ["still open thing"]
    assert slim["resolution"]["unresolved_ack"] == {"0": {"by": "dashboard"}}
    assert slim["resolution"]["commit"] == "abc1234"
    assert [(e["event"], e["at"]) for e in slim["history"]] == [
        ("claim", "2026-07-26T14:00:00Z"), ("close", "2026-07-26T15:00:00Z"),
    ]
    assert slim["history"][0]["by"] == {"worker": "w1", "session_id": "s1"}
    assert "resolution" not in slim["history"][1]
    assert len(slim["history"][1]["text"]) <= server._SLIM_EVENT_TEXT_MAX + 1


def test_source_rows_are_never_mutated():
    # The untrimmed memo is what /api/ux-fixes/item falls back to.
    server = _load_server()
    src = _closed_row()
    server._ux_fixes_slim_items([src])
    assert src["_github_body"] == _LONG
    assert src["resolution"]["summary"] == _LONG
    assert src["history"][1]["resolution"] == {"summary": _LONG}
    assert "_slim" not in src


def test_open_rows_ship_whole():
    server = _load_server()
    src = _open_row()
    out = server._ux_fixes_slim_items([src])[0]
    assert out is src


def test_slim_copy_is_memoized_per_snapshot():
    server = _load_server()
    snapshot = [_closed_row(), _open_row()]
    first = server._ux_fixes_slim_items(snapshot)
    assert server._ux_fixes_slim_items(snapshot) is first
    assert server._ux_fixes_slim_items(list(snapshot)) is not first
