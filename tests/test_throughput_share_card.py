"""Share card: totals-only aggregation, caching, and the no-names rule."""
import importlib
import json
import sys
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

server = importlib.import_module("server")
# Functions inside ccc_server.usage_stats resolve helpers in that module's own
# globals, so patches must target it (patching `server` would not reach them).
usage = sys.modules["ccc_server.usage_stats"]
ROOT = Path(__file__).resolve().parent.parent

SECRET_NAME = "SECRET-PROJECT-ALPHA"
SECRET_PATH = "/Users/someone/private/client-repo"


def _turn(day_offset, now, tokens_in=1000, tokens_out=200, cache=800, dur=30.0, **extra):
    end = datetime.fromtimestamp(now).astimezone() - timedelta(days=day_offset)
    t = {
        "t_start": (end - timedelta(seconds=dur)).isoformat(),
        "t_end": end.isoformat(),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "raw_context_tokens": tokens_in,
        "cache_read_tokens": cache,
        "dur_sec": dur,
        "cost_usd": 0.5,
        # Identifying fields that must never reach the share payload.
        "session_id": "11111111-2222-3333-4444-555555555555",
        "session_name": SECRET_NAME,
        "folder_path": SECRET_PATH,
        "trigger_preview": f"please edit {SECRET_PATH}",
        "assistant_preview": SECRET_NAME,
    }
    t.update(extra)
    return t


def test_aggregate_buckets_by_local_day_and_engine():
    now = time.time()
    payload = usage._throughput_share_aggregate(
        {
            "claude": [_turn(0, now), _turn(0, now), _turn(3, now)],
            "codex": [_turn(0, now, tokens_in=500, tokens_out=100)],
            "kimi": [],
        },
        now=now,
    )
    by_day = {r["day"]: r for r in payload["daily"]}
    today = datetime.fromtimestamp(now).astimezone().strftime("%Y-%m-%d")
    assert by_day[today]["turns"] == 3
    assert by_day[today]["tokens"] == 2 * 1200 + 600
    assert by_day[today]["engine_tokens"] == {"claude": 2400, "codex": 600}
    assert payload["engines"] == ["claude", "codex"]
    assert len(payload["daily"]) == 2


def test_aggregate_drops_turns_older_than_365_days():
    now = time.time()
    payload = usage._throughput_share_aggregate(
        {"claude": [_turn(400, now), _turn(10, now)]}, now=now
    )
    assert len(payload["daily"]) == 1


def test_payload_never_contains_session_names_paths_or_ids():
    now = time.time()
    payload = usage._throughput_share_aggregate(
        {"claude": [_turn(0, now), _turn(1, now)]}, now=now
    )
    blob = json.dumps(payload)
    for needle in (SECRET_NAME, SECRET_PATH, "11111111-2222", "please edit"):
        assert needle not in blob
    allowed = {
        "day", "tokens", "raw_context_tokens", "cache_read_tokens", "turns",
        "active_duration_sec", "cost_usd", "engine_tokens",
    }
    for row in payload["daily"]:
        assert set(row) == allowed


def test_share_payload_is_cached_by_ttl(monkeypatch):
    now = time.time()
    calls = []

    def fake_window_turns(start, end, engine):
        calls.append((engine, start))
        return [_turn(0, now)] if engine == "claude" else []

    monkeypatch.setattr(usage, "_throughput_window_turns", fake_window_turns)
    usage._THROUGHPUT_SHARE_CACHE.update({"ts": 0.0, "payload": None})
    usage._THROUGHPUT_SHARE_JOB["thread"] = None

    first, status = usage._throughput_share_payload(wait=5)
    assert status == 200 and first["daily"]
    assert sorted(e for e, _ in calls) == ["claude", "codex", "kimi"]
    # Discovery is bounded to the 365-day window, never "all time".
    assert all(now - 366 * 86400 < s < now - 364 * 86400 for _, s in calls)

    for _ in range(5):
        again, _ = usage._throughput_share_payload(wait=5)
        assert again is first
    assert len(calls) == 3, f"share payload recomputed inside TTL: {len(calls)} calls"


def test_share_build_failure_degrades_to_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(usage, "_throughput_window_turns", boom)
    payload = usage._throughput_share_build()
    assert payload["daily"] == []


def test_card_renderer_reads_only_totals_fields():
    """The canvas card code must not reference identifying fields."""
    html = (ROOT / "static" / "throughput.html").read_text()
    start = html.index("// SHARE-CARD:BEGIN")
    end = html.index("// SHARE-CARD:END")
    src = html[start:end]
    for banned in ("session_name", "session_id", "folder_path", "display_name",
                   "jsonl_path", "trigger_preview", "assistant_preview",
                   "repo_path", "session-title", "sid-label"):
        assert banned not in src, banned


def test_share_copy_has_no_em_dashes():
    html = (ROOT / "static" / "throughput.html").read_text()
    start = html.index("// SHARE-CARD:BEGIN")
    end = html.index("// SHARE-CARD:END")
    assert "—" not in html[start:end]
    assert "&mdash;" not in html[start:end]


def _node(*args):
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not installed")
    return subprocess.run([node, *args], cwd=ROOT, capture_output=True, text=True, timeout=120)


def test_share_text_uses_card_url_when_upload_succeeds():
    """Card page URL is the only link on success; repo link fallback on failure."""
    r = _node("tests/share_card_upload_harness.cjs")
    assert r.returncode == 0, r.stdout + r.stderr


def test_card_worker_unit_tests():
    r = _node("--test", "infra/card-worker/index.test.mjs")
    assert r.returncode == 0, r.stdout + r.stderr


def test_share_screen_discloses_upload():
    html = (ROOT / "static" / "throughput.html").read_text()
    assert "Sharing uploads only this image to make the link preview." in html


def test_month_card_harness():
    """'My October' card: month picking, totals, model, post text, deep link, no names."""
    r = _node("tests/share_card_month_harness.cjs")
    assert r.returncode == 0, r.stdout + r.stderr


def test_month_card_one_click_entry_points():
    html = (ROOT / "static" / "throughput.html").read_text()
    assert 'id="month-card-btn"' in html
    assert 'data-v="cal"' in html
