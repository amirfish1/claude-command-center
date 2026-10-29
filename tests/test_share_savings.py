"""Share-card savings variant: $ saved / $0 runs metrics end to end.

Backend: free sessions are detected from the spawn registry / marker files
(runtime="free") and their turns' API-priced cost becomes the saved amount.
Frontend: the picker gains $ saved / $0 runs, the post text says
"My AI agents did $X of work for $0", and milestones prompt a share.
"""
import importlib
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

server = importlib.import_module("server")
usage = sys.modules["ccc_server.usage_stats"]
share_savings = sys.modules["ccc_server.share_savings"]
ROOT = Path(__file__).resolve().parent.parent

FREE_SID = "freeeeee-0000-0000-0000-000000000001"
PAID_SID = "paid0000-0000-0000-0000-000000000002"
SECRET_NAME = "SECRET-PROJECT-ALPHA"


def _turn(day_offset, now, sid=PAID_SID, cost=0.5, tokens_in=1000, tokens_out=200, **extra):
    end = datetime.fromtimestamp(now).astimezone() - timedelta(days=day_offset)
    t = {
        "session_id": sid,
        "t_end": end.isoformat(),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost,
        "session_name": SECRET_NAME,
        "folder_path": "/Users/someone/private/client-repo",
    }
    t.update(extra)
    return t


def test_free_session_ids_from_registry(monkeypatch):
    monkeypatch.setattr(server, "_spawned_sessions", [])
    monkeypatch.setattr(
        server,
        "_load_spawn_registry",
        lambda: [
            {"session_id": FREE_SID, "runtime": "free"},
            {"session_id": PAID_SID, "runtime": "plan"},
            {"session_id": "other", "model": "x"},
        ],
    )
    monkeypatch.setattr(server, "SPAWN_MARKERS_DIR", Path("/nonexistent-dir"))
    assert share_savings.free_session_ids() == {FREE_SID}


def test_free_session_ids_from_resumed_sid_and_markers(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_spawned_sessions", [])
    monkeypatch.setattr(
        server,
        "_load_spawn_registry",
        lambda: [{"resumed_sid": "resumed-1", "runtime": "free"}],
    )
    markers = tmp_path / "markers"
    markers.mkdir()
    (markers / "markfree.json").write_text(json.dumps({"kind": "free"}))
    (markers / "markpaid.json").write_text(json.dumps({"kind": "worker"}))
    (markers / "broken.json").write_text("{not json")
    monkeypatch.setattr(server, "SPAWN_MARKERS_DIR", markers)
    assert share_savings.free_session_ids() == {"resumed-1", "markfree"}


def test_free_session_ids_empty_when_nothing_marked(monkeypatch):
    monkeypatch.setattr(server, "_spawned_sessions", [])
    monkeypatch.setattr(server, "_load_spawn_registry", lambda: [])
    monkeypatch.setattr(server, "SPAWN_MARKERS_DIR", Path("/nonexistent-dir"))
    assert share_savings.free_session_ids() == set()


def test_savings_block_buckets_and_counts_runs_per_session():
    now = time.time()
    turns = {
        "claude": [
            _turn(0, now, sid=FREE_SID, cost=1.25, tokens_in=5000, tokens_out=500),
            _turn(0, now, sid=FREE_SID, cost=0.75),       # same session, same day
            _turn(2, now, sid=FREE_SID, cost=2.0),        # same session, another day
            _turn(0, now, sid=PAID_SID, cost=9.99),       # not free: ignored
        ],
        "codex": [],
    }
    block = share_savings.savings_block(
        {"daily": []}, turns, free_sids={FREE_SID}, now=now
    )
    today = datetime.fromtimestamp(now).astimezone().strftime("%Y-%m-%d")
    two_days_ago = (
        datetime.fromtimestamp(now).astimezone() - timedelta(days=2)
    ).strftime("%Y-%m-%d")
    by_day = {r["day"]: r for r in block["daily"]}
    assert by_day[today]["free_saved_usd"] == 2.0
    assert by_day[today]["free_runs"] == 1          # one session, two turns
    assert by_day[today]["free_tokens"] == 6700
    assert by_day[two_days_ago]["free_saved_usd"] == 2.0
    assert by_day[two_days_ago]["free_runs"] == 1   # per-day count, not per-window
    assert block["free_saved_usd"] == 4.0
    assert block["free_runs"] == 1                  # distinct sessions in window
    assert block["free_tokens"] == 7900
    assert block["available"] is True


def test_savings_block_drops_out_of_window_turns():
    now = time.time()
    block = share_savings.savings_block(
        {"daily": []},
        {"claude": [_turn(400, now, sid=FREE_SID, cost=5.0)]},
        free_sids={FREE_SID},
        now=now,
    )
    assert block["daily"] == []
    assert block["free_saved_usd"] == 0.0
    assert block["available"] is True               # the session exists, the data doesn't


def test_savings_never_leaks_session_identifiers():
    now = time.time()
    block = share_savings.savings_block(
        {"daily": []},
        {"claude": [_turn(0, now, sid=FREE_SID)]},
        free_sids={FREE_SID},
        now=now,
    )
    blob = json.dumps(block)
    for needle in (FREE_SID, SECRET_NAME, "private/client-repo"):
        assert needle not in blob


def test_share_payload_carries_savings(monkeypatch):
    now = time.time()
    monkeypatch.setattr(
        usage,
        "_throughput_window_turns",
        lambda start, end, engine: (
            [_turn(0, now, sid=FREE_SID, cost=3.0)] if engine == "claude" else []
        ),
    )
    monkeypatch.setattr(
        share_savings, "free_session_ids", lambda: {FREE_SID}
    )
    usage._THROUGHPUT_SHARE_CACHE.update({"ts": 0.0, "payload": None})
    usage._THROUGHPUT_SHARE_JOB["thread"] = None
    payload, status = usage._throughput_share_payload(wait=5)
    assert status == 200
    assert payload["savings"]["free_saved_usd"] == 3.0
    assert payload["savings"]["free_runs"] == 1
    # The daily token rows keep their exact totals-only key set.
    allowed = {
        "day", "tokens", "raw_context_tokens", "cache_read_tokens", "turns",
        "active_duration_sec", "cost_usd", "engine_tokens",
    }
    for row in payload["daily"]:
        assert set(row) == allowed


def test_annotate_never_raises_on_weird_input():
    payload = {"daily": []}
    out = share_savings.annotate_share_payload(payload, {"claude": None}, free_sids=set())
    assert out["savings"]["available"] is False
    assert out["savings"]["daily"] == []


# ── Static checks on the picker + copy ──

def _share_block_src():
    html = (ROOT / "static" / "throughput.html").read_text()
    start = html.index("// SHARE-CARD:BEGIN")
    end = html.index("// SHARE-CARD:END")
    return html, html[start:end]


def test_picker_offers_savings_metrics():
    html, _ = _share_block_src()
    assert 'data-v="saved"' in html
    assert 'data-v="runs"' in html
    assert 'id="sh-opt-saved"' in html


def test_mandated_card_text_present():
    _, src = _share_block_src()
    assert "of work for $0" in src
    assert "runs for $0" in src
    # No em-dashes or banned identifiers in the new copy either.
    assert "—" not in src
    for banned in ("session_name", "session_id", "folder_path"):
        assert banned not in src


def test_savings_js_harness():
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not installed")
    r = subprocess.run(
        [node, "tests/share_card_savings_harness.cjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
