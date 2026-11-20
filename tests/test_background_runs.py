from concurrent.futures import ThreadPoolExecutor
import json
import os
import threading

import pytest

import server  # noqa: F401  (registers core names)
from ccc_server import background_runs


def _write(root, slug, sid, mtime, usage):
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.jsonl"
    p.write_text(json.dumps({
        "type": "assistant", "sessionId": sid, "timestamp": "2026-10-16T10:00:00Z",
        "message": {"id": "m-" + sid, "model": "claude-x", "usage": usage},
    }) + "\n")
    os.utime(p, (mtime, mtime))


def test_groups_temp_dir_runs_by_spawner_and_ignores_real_projects(tmp_path):
    now = 1_800_000_000.0
    u = {"input_tokens": 1, "cache_read_input_tokens": 10, "output_tokens": 2}
    _write(tmp_path, "-private-var-folders-ab-T", "a1", now - 60, u)
    _write(tmp_path, "-private-var-folders-ab-T", "a2", now - 7200, u)
    _write(tmp_path, "-private-var-folders-ab-T", "b1", now - 60, u)
    _write(tmp_path, "-Users-me-Apps-repo", "r1", now - 60, u)
    markers = {"a1": {"spawned_via": "evalx", "kind": "grader"},
               "a2": {"spawned_via": "evalx", "kind": "grader"}}

    p = background_runs.background_runs_payload(
        now=now, projects_root=tmp_path, markers=markers, use_cache=False)

    by = {g["spawner"]: g for g in p["groups"]}
    assert by["evalx"]["runs"] == 2 and by["evalx"]["runs_last_hour"] == 1
    assert by["evalx"]["tokens_last_day"] == 26  # both runs are inside the day window
    assert by["unknown"]["runs"] == 1
    assert p["totals"]["runs"] == 3
    assert by["evalx"]["cost_usd"] > 0


def test_payload_reports_cpu_pressure(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "getloadavg", lambda: (9.0, 4.0, 2.0))
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    p = background_runs.background_runs_payload(now=1_800_000_000.0, projects_root=tmp_path, markers={}, use_cache=False)
    assert p["cpu"]["cores"] == 4
    assert p["cpu"]["load_per_core"] == 2.25
    assert p["cpu"]["level"] == "saturated"


def test_payload_reports_memory_pressure(tmp_path, monkeypatch):
    real_open = open
    meminfo = "MemTotal: 1000000 kB\nMemAvailable: 50000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"

    def fake_open(path, *a, **kw):
        if path == "/proc/meminfo":
            import io
            return io.StringIO(meminfo)
        if path == "/proc/pressure/memory":
            raise OSError("no psi")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", fake_open)
    p = background_runs.background_runs_payload(now=1_800_000_000.0, projects_root=tmp_path, markers={}, use_cache=False)
    assert p["memory"]["used_pct"] == 95.0
    assert p["memory"]["level"] == "saturated"
    assert "swap_used_pct" not in p["memory"]


def test_concurrent_cache_misses_share_one_scan(tmp_path, monkeypatch):
    now = 1_800_000_000.0
    _write(tmp_path, "-tmp-probe", "a1", now - 60, {"input_tokens": 1})
    monkeypatch.setattr(background_runs, "_CACHE", {"ts": 0, "payload": None})
    monkeypatch.setattr(background_runs, "_TOKEN_MEMO", {})
    scan_started = threading.Event()
    follower_missed = threading.Event()
    scan_calls = []
    scan = background_runs._temp_transcripts
    cached_payload = background_runs._cached_payload

    def observe_cache(at):
        payload = cached_payload(at)
        if at == now + 1 and payload is None:
            follower_missed.set()
        return payload

    def gated_scan(root):
        scan_calls.append(root)
        scan_started.set()
        assert follower_missed.wait(5)
        return scan(root)

    monkeypatch.setattr(background_runs, "_cached_payload", observe_cache)
    monkeypatch.setattr(background_runs, "_temp_transcripts", gated_scan)
    monkeypatch.setattr(background_runs, "cpu_pressure",
                        lambda: {"reader": threading.get_ident()})
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(background_runs.background_runs_payload, now, tmp_path, {})
        assert scan_started.wait(5)
        second = pool.submit(background_runs.background_runs_payload, now + 1, tmp_path, {})
        first_result = first.result(timeout=5)
        second_result = second.result(timeout=5)

    assert scan_calls == [tmp_path]
    assert first_result["groups"] == second_result["groups"]
    assert first_result["totals"]["runs"] == 1
    assert first_result["cpu"] != second_result["cpu"]
    assert background_runs._CACHE["ts"] == now


def test_failed_cached_scan_is_retried(tmp_path, monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr(background_runs, "_CACHE", {"ts": 0, "payload": None})
    scan = background_runs._temp_transcripts

    def failed_scan(root):
        raise OSError("scan failed")

    monkeypatch.setattr(background_runs, "_temp_transcripts", failed_scan)
    with pytest.raises(OSError, match="scan failed"):
        background_runs.background_runs_payload(now, tmp_path, {})
    assert background_runs._CACHE["payload"] is None

    monkeypatch.setattr(background_runs, "_temp_transcripts", scan)
    assert background_runs.background_runs_payload(now, tmp_path, {})["ok"]
