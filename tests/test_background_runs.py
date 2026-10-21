import json
import os

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
