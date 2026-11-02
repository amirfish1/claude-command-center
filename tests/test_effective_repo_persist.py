"""Effective-repo inference survives a restart (CCC-298).

After a restart the first sidebar load of a repo re-read every drifted
session's transcript and shelled out to git: one /api/conversations call took
18 s. The newest result per session is persisted, keyed by the JSONL mtime.
"""
import json

import pytest

import server  # noqa: F401  (binds the _core proxy; reloads morning_launch)
from ccc_server import morning_launch as ml


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    f = tmp_path / "effective_repo_cache.json"
    monkeypatch.setattr(ml, "_EFFECTIVE_REPO_DISK_FILE", f)
    dicts = (ml._EFFECTIVE_REPO_CACHE, ml._EFFECTIVE_REPO_SID_LATEST, ml._EFFECTIVE_REPO_PERSIST)
    saved = [dict(d) for d in dicts]
    _forget_memory()
    monkeypatch.setitem(ml._effective_repo_persist_state, "dirty", False)
    monkeypatch.setitem(ml._effective_repo_persist_state, "saved_at", 0.0)
    yield f
    for d, old in zip(dicts, saved):
        d.clear()
        d.update(old)


def _forget_memory():
    for d in (ml._EFFECTIVE_REPO_CACHE, ml._EFFECTIVE_REPO_SID_LATEST, ml._EFFECTIVE_REPO_PERSIST):
        d.clear()


def test_saved_result_is_a_cache_hit_after_restart(fresh, monkeypatch):
    eff = {"top": "/r/wt", "count": 3, "total": 4, "branch": "feat", "kind": "worktree", "ahead": 1, "behind": 0}
    ml._effective_repo_cache_put(("sid-a", 1700000000.123456, "/r/main", "/r/main"), eff)
    ml._effective_repo_cache_put(("sid-b", 1700000001.5, "/r/x", None), None)
    assert ml._save_effective_repo_cache(force=True)

    _forget_memory()
    assert ml._load_effective_repo_cache() == 2

    def boom(*a, **k):
        raise AssertionError("transcript re-scanned")

    monkeypatch.setattr(ml._core, "_scan_session_tool_paths", boom)
    assert ml._infer_effective_repo("sid-a", literal_cwd="/r/main", exclude_top="/r/main",
                                    jsonl_mtime=1700000000.123456) == eff
    assert ml._infer_effective_repo("sid-b", literal_cwd="/r/x", jsonl_mtime=1700000001.5) is None


def test_only_the_newest_entry_per_session_is_persisted(fresh):
    ml._effective_repo_cache_put(("sid-a", 1.0, "/c", None), None)
    ml._effective_repo_cache_put(("sid-a", 2.0, "/c", None), {"top": "/t"})
    ml._save_effective_repo_cache(force=True)
    data = json.loads(fresh.read_text())
    assert data["entries"] == [[["sid-a", 2.0, "/c", None], {"top": "/t"}]]


def test_saves_are_throttled_and_skipped_when_clean(fresh):
    assert not ml._save_effective_repo_cache()  # nothing dirty
    ml._effective_repo_cache_put(("s", 1.0, "/c", None), None)
    assert ml._save_effective_repo_cache()
    ml._effective_repo_cache_put(("s", 2.0, "/c", None), None)
    assert not ml._save_effective_repo_cache()  # within the throttle window
    assert ml._save_effective_repo_cache(force=True)


def test_bad_or_old_files_are_ignored(fresh):
    fresh.write_text("{not json")
    assert ml._load_effective_repo_cache() == 0
    fresh.write_text(json.dumps({"schema": 999, "entries": [[["s", 1, None, None], None]]}))
    assert ml._load_effective_repo_cache() == 0
    fresh.write_text(json.dumps({"schema": 1, "entries": [["bad"], [["s", 1, None], None], [["s", 1, None, None], 5]]}))
    assert ml._load_effective_repo_cache() == 0
