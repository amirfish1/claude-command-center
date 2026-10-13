"""Cloud relay presence: only human input (terminal prompts, dashboard composer)
counts, never relay, peer or WatchTower injections. No server."""
import json
import os
import sys
import time
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import pytest

from ccc_server import cloud_relay as cr
from ccc_server import presence

SID = "aaaaaaaa-2222-3333-4444-555555555555"


def _iso(ep):
    return datetime.fromtimestamp(ep, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _user(text, ago=10, **extra):
    ev = {"type": "user", "timestamp": _iso(time.time() - ago),
          "message": {"role": "user", "content": text}}
    ev.update(extra)
    return ev


def _assistant(text="ok"):
    return {"type": "assistant", "message": {"role": "assistant", "content": text}}


def _write(path, events):
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return str(path)


class API:
    def __init__(self, rows):
        self.rows = rows
        self.path_calls = 0

    def get(self, path, query=None):
        if path == "/api/sessions":
            if "jsonl_path" in (query or {}).get("fields", ""):
                self.path_calls += 1
            return 200, {"sessions": self.rows}
        if path == "/api/attention":
            return 200, {"items": []}
        return 200, {"ok": True}

    def post(self, path, body=None):
        return 200, {"ok": True}

    def call(self, method, path, body=None, query=None, timeout=None):
        return self.get(path, query)


def _row(path, **over):
    r = {"id": SID, "session_id": SID, "display_name": "Work", "state": "idle",
         "is_live": True, "mtime": time.time(), "jsonl_path": path}
    r.update(over)
    return r


@pytest.fixture(autouse=True)
def _clean():
    presence.reset_for_tests()
    yield
    presence.reset_for_tests()


def _snap(rows):
    return cr.build_snapshot(API(rows), {"share_titles": True})


def test_terminal_prompt_makes_present(tmp_path):
    p = _write(tmp_path / "s.jsonl", [
        _user("spawn prompt", ago=900), _assistant(),
        _user("please fix the bug", ago=20), _assistant()])
    snap = _snap([_row(p)])
    assert snap["presence"]["present"] is True
    assert snap["sessions"][0]["last_human_input_at"]
    assert snap["presence"]["last_input_at"] == snap["sessions"][0]["last_human_input_at"]


def test_old_input_is_not_present(tmp_path):
    p = _write(tmp_path / "s.jsonl", [
        _user("first", ago=4000), _assistant(), _user("typed", ago=3600)])
    snap = _snap([_row(p)])
    assert snap["presence"]["present"] is False
    assert snap["presence"]["last_input_at"]


def test_no_input_has_null_presence(tmp_path):
    p = _write(tmp_path / "s.jsonl", [_user("only spawn prompt"), _assistant()])
    snap = _snap([_row(p)])
    assert snap["presence"] == {"present": False, "last_input_at": None}
    assert "last_human_input_at" not in snap["sessions"][0]


@pytest.mark.parametrize("text", [
    "[From Instinct via CCC Cloud, request abc123]\nhello",
    "hi\n\n[Sent from a remote client through CCC Cloud Relay. Reply with x]",
    "Announced from: peer-session\nhello",
    "<system-reminder>stuff</system-reminder>",
    "<task-notification>done</task-notification>",
])
def test_provenance_markers_are_not_human(tmp_path, text):
    p = _write(tmp_path / "s.jsonl", [
        _user("spawn", ago=900), _assistant(), _user(text, ago=5)])
    snap = _snap([_row(p)])
    assert snap["presence"]["present"] is False


def test_tool_results_meta_and_sidechain_are_not_human(tmp_path):
    result = {"type": "user", "timestamp": _iso(time.time() - 3),
              "message": {"role": "user", "content": [
                  {"type": "tool_result", "tool_use_id": "t", "content": "x"}]}}
    p = _write(tmp_path / "s.jsonl", [
        _user("spawn", ago=900), _assistant(), result,
        _user("meta", ago=4, isMeta=True), _user("side", ago=4, isSidechain=True)])
    assert _snap([_row(p)])["presence"]["present"] is False


def test_machine_inject_echo_is_not_human(tmp_path):
    text = "run the migration and report back"
    presence.note_inject(SID, text, "api")
    presence.note_inject(SID, "from watchtower ticket", "wt")
    p = _write(tmp_path / "s.jsonl", [
        _user("spawn", ago=900), _assistant(), _user(text, ago=5),
        _user("from watchtower ticket now", ago=2)])
    assert _snap([_row(p)])["presence"]["present"] is False


def test_composer_send_is_human_without_transcript_echo(tmp_path):
    p = _write(tmp_path / "s.jsonl", [_user("spawn", ago=900), _assistant()])
    presence.note_inject(SID, "hello from ui", "composer")
    snap = _snap([_row(p)])
    assert snap["presence"]["present"] is True
    assert snap["sessions"][0]["last_human_input_at"]


@pytest.mark.parametrize("source", ["cloud-relay", "api", "wt", "peer"])
def test_non_composer_sources_never_stamp(tmp_path, source):
    p = _write(tmp_path / "s.jsonl", [_user("spawn", ago=900), _assistant()])
    presence.note_inject(SID, "hello", source)
    assert _snap([_row(p)])["presence"]["present"] is False


def test_only_live_sessions_are_scanned(tmp_path):
    p = _write(tmp_path / "s.jsonl", [
        _user("spawn", ago=900), _assistant(), _user("typed", ago=5)])
    snap = _snap([_row(p, is_live=False)])
    assert snap["presence"]["present"] is False
    assert "last_human_input_at" not in snap["sessions"][0]


def test_steady_state_skips_path_fetch_and_scan(tmp_path):
    p = _write(tmp_path / "s.jsonl", [
        _user("spawn", ago=900), _assistant(), _user("typed", ago=5)])
    row = _row(p, mtime=1000.0)
    api = API([row])
    cr.build_snapshot(api, {"share_titles": True})
    first = api.path_calls
    for _ in range(5):
        cr.build_snapshot(api, {"share_titles": True})
    assert api.path_calls == first


def test_presence_survives_delta_and_digest(tmp_path):
    p = _write(tmp_path / "s.jsonl", [
        _user("spawn", ago=900), _assistant(), _user("typed", ago=5)])
    a = _snap([_row(p, mtime=1000.0)])
    presence.reset_for_tests()
    p2 = _write(tmp_path / "s2.jsonl", [_user("spawn", ago=900), _assistant()])
    b = _snap([_row(p2, mtime=1000.0)])
    assert cr.snapshot_digest(a) != cr.snapshot_digest(b)
    delta = cr.build_delta(a, b, "base", cr.snapshot_digest(b))
    assert delta["presence"] == {"present": False, "last_input_at": None}
