"""Moment Zero fresh-install detection (ccc_server.moment_zero).

The verdict drives whether the onboarding overlay may auto-open, so the scan
must be right *and* stay cheap: these tests pin both. The counter argument
counts every directory entry the scan inspects, which is what makes the
bound assertions machine-independent.
"""
import json
import os

import pytest

from ccc_server import moment_zero


@pytest.fixture(autouse=True)
def _fresh_cache():
    moment_zero.reset_cache()
    yield
    moment_zero.reset_cache()


def test_empty_home_is_fresh(tmp_path):
    state = moment_zero.moment_zero_state(home=tmp_path)
    assert state["ok"] is True
    assert state["fresh_install"] is True
    assert state["has_history"] is False
    assert state["signals"] == {
        "onboarding_completed": False,
        "claude_transcripts": False,
        "codex_sessions": False,
    }


def test_claude_transcript_marks_history(tmp_path):
    proj = tmp_path / ".claude" / "projects" / "-Users-x-repo"
    proj.mkdir(parents=True)
    (proj / "session-1.jsonl").write_text("{}\n")
    state = moment_zero.moment_zero_state(home=tmp_path)
    assert state["has_history"] is True
    assert state["fresh_install"] is False
    assert state["signals"]["claude_transcripts"] is True


def test_top_level_jsonl_counts(tmp_path):
    projects = tmp_path / ".claude" / "projects"
    projects.mkdir(parents=True)
    (projects / "stray.jsonl").write_text("{}\n")
    assert moment_zero.moment_zero_state(home=tmp_path)["has_history"] is True


def test_codex_sessions_marks_history(tmp_path):
    day = tmp_path / ".codex" / "sessions" / "2026" / "10" / "05"
    day.mkdir(parents=True)
    (day / "rollout.jsonl").write_text("{}\n")
    state = moment_zero.moment_zero_state(home=tmp_path)
    assert state["has_history"] is True
    assert state["signals"]["codex_sessions"] is True


def test_completed_onboarding_marks_history(tmp_path):
    state_dir = tmp_path / ".claude" / "command-center"
    state_dir.mkdir(parents=True)
    (state_dir / "onboarding.json").write_text(json.dumps({"completed": True}))
    state = moment_zero.moment_zero_state(home=tmp_path)
    assert state["has_history"] is True
    assert state["signals"]["onboarding_completed"] is True


def test_empty_dirs_alone_stay_fresh(tmp_path):
    (tmp_path / ".claude" / "projects" / "-Users-x-repo").mkdir(parents=True)
    (tmp_path / ".codex" / "sessions").mkdir(parents=True)
    state = moment_zero.moment_zero_state(home=tmp_path)
    assert state["fresh_install"] is True


def test_first_transcript_inside_existing_project_dir_flips_verdict(tmp_path):
    proj = tmp_path / ".claude" / "projects" / "-Users-x-repo"
    proj.mkdir(parents=True)
    assert moment_zero.moment_zero_state(home=tmp_path)["fresh_install"] is True
    # Directory mtimes move in coarse steps on some filesystems; force the
    # subdir mtime forward so the signature cannot alias the earlier scan.
    (proj / "session-1.jsonl").write_text("{}\n")
    os.utime(proj, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
    assert moment_zero.moment_zero_state(home=tmp_path)["has_history"] is True


def test_scan_is_bounded_on_huge_projects_tree(tmp_path):
    projects = tmp_path / ".claude" / "projects"
    projects.mkdir(parents=True)
    for i in range(600):
        (projects / f"proj-{i:04d}").mkdir()
    counter = [0]
    state = moment_zero.moment_zero_state(home=tmp_path, counter=counter)
    # 500+ project dirs means prior agent use by definition — and the walk
    # must have stopped at the cap instead of scandir-ing all 600.
    assert state["has_history"] is True
    assert counter[0] <= moment_zero._MAX_PROJECT_DIRS + 2


def test_cached_verdict_skips_rescan(tmp_path):
    proj = tmp_path / ".claude" / "projects" / "-Users-x-repo"
    proj.mkdir(parents=True)
    first = moment_zero.moment_zero_state(home=tmp_path, counter=[0])
    assert first["fresh_install"] is True
    counter = [0]
    second = moment_zero.moment_zero_state(home=tmp_path, counter=counter)
    assert second == first
    assert counter[0] == 0  # unchanged signature: no directory walk at all


def test_history_verdict_is_sticky(tmp_path):
    proj = tmp_path / ".claude" / "projects" / "-Users-x-repo"
    proj.mkdir(parents=True)
    transcript = proj / "session-1.jsonl"
    transcript.write_text("{}\n")
    assert moment_zero.moment_zero_state(home=tmp_path)["has_history"] is True
    transcript.unlink()
    counter = [0]
    state = moment_zero.moment_zero_state(home=tmp_path, counter=counter)
    assert state["has_history"] is True
    assert counter[0] == 0  # sticky hit: not even a signature check rescans
