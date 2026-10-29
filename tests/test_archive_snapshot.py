"""Archive snapshots must survive independent refresh workers publishing at once."""

import json
import multiprocessing
from pathlib import Path

import pytest

from ccc_server import archive_snapshot


def snapshot(key="base", stamp=10, signature="sig-a", sid="session-a"):
    return {
        "schema_version": 9,
        "build_id": "9",
        "entries": {key: {
            "cached_at": stamp,
            "signature": signature,
            "conversations": [{"session_id": sid}],
        }},
        "signature_maps": {signature: {
            "files": {sid + ".jsonl": [123, 456]}, "extras": {},
        }},
    }


def read(cache_file):
    return json.loads(cache_file.read_text())


def test_stale_worker_preserves_newer_variants_and_their_stat_maps(tmp_path):
    cache_file = tmp_path / "archive.json"
    assert archive_snapshot.save(cache_file, snapshot())
    assert archive_snapshot.save(cache_file, snapshot(stamp=30, signature="sig-b", sid="session-b"))
    stale = snapshot()  # another worker loaded this before the newer publication
    other = snapshot("prs", 20, "sig-c", "session-c")
    stale["entries"].update(other["entries"])
    stale["signature_maps"].update(other["signature_maps"])
    assert archive_snapshot.save(cache_file, stale)
    actual = read(cache_file)
    assert actual["entries"]["base"]["conversations"] == [{"session_id": "session-b"}]
    assert actual["entries"]["prs"]["conversations"] == [{"session_id": "session-c"}]
    assert set(actual["signature_maps"]) == {"sig-b", "sig-c"}
    assert actual["signature_maps"]["sig-b"]["files"] == {"session-b.jsonl": [123, 456]}


@pytest.mark.parametrize("previous", ["{broken", "[]", '{"entries": []}'])
def test_corrupt_cache_recovers(tmp_path, previous):
    cache_file = tmp_path / "archive.json"
    cache_file.write_text(previous)
    assert archive_snapshot.save(cache_file, snapshot())
    assert read(cache_file) == snapshot()


@pytest.mark.parametrize("field", ["schema_version", "build_id"])
def test_incompatible_snapshot_is_not_merged(tmp_path, field):
    cache_file = tmp_path / "archive.json"
    old = snapshot("old")
    old[field] = "obsolete"
    cache_file.write_text(json.dumps(old))
    assert archive_snapshot.save(cache_file, snapshot())
    assert read(cache_file) == snapshot()


def test_malformed_entries_do_not_poison_healthy_variants(tmp_path):
    cache_file = tmp_path / "archive.json"
    old = snapshot()
    old["entries"].update({
        "not-an-entry": [],
        "not-rows": {"cached_at": 100, "conversations": {}},
        "bad-time": {"cached_at": "bad", "conversations": []},
        "infinite-time": {"cached_at": float("inf"), "conversations": []},
    })
    cache_file.write_text(json.dumps(old))
    assert archive_snapshot.save(cache_file, snapshot("prs"))
    assert set(read(cache_file)["entries"]) == {"base", "prs"}


def test_failed_replace_keeps_previous_snapshot_and_removes_temp(tmp_path, monkeypatch):
    cache_file = tmp_path / "archive.json"
    assert archive_snapshot.save(cache_file, snapshot())
    previous = cache_file.read_bytes()

    def fail(*args):
        raise OSError("replacement failed")

    monkeypatch.setattr(archive_snapshot.os, "replace", fail)
    with pytest.raises(OSError, match="replacement failed"):
        archive_snapshot.save(cache_file, snapshot(stamp=20))
    assert cache_file.read_bytes() == previous
    assert list(tmp_path.glob("*.tmp")) == []


def test_busy_lock_skips_within_budget_and_can_retry(tmp_path):
    cache_file = tmp_path / "archive.json"
    with archive_snapshot._writer_lock(cache_file, timeout=1) as acquired:
        assert acquired
        assert archive_snapshot.save(cache_file, snapshot(), lock_timeout=0) is False
        assert not cache_file.exists()
    assert archive_snapshot.save(cache_file, snapshot(), lock_timeout=0)


def _publish_worker(cache_file, key, start):
    start.wait(10)
    assert archive_snapshot.save(Path(cache_file), snapshot(key), lock_timeout=5)


def test_independent_processes_preserve_all_variants(tmp_path):
    cache_file = tmp_path / "archive.json"
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    workers = [ctx.Process(target=_publish_worker, args=(str(cache_file), key, start))
               for key in ("base", "prs", "worktrees")]
    try:
        for worker in workers:
            worker.start()
        start.set()
        for worker in workers:
            worker.join(15)
            assert worker.exitcode == 0
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(5)
    assert set(read(cache_file)["entries"]) == {"base", "prs", "worktrees"}
    assert list(tmp_path.glob("*.tmp")) == []


def test_server_debounce_only_counts_successful_publication(tmp_path, monkeypatch):
    import server

    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE_FILE", tmp_path / "archive.json")
    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE", snapshot()["entries"])
    monkeypatch.setattr(server, "_ARCHIVE_STATMAP_BY_SIG", {})
    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE_LAST_SAVE_AT", 0)
    calls = []

    def save(*args, **kwargs):
        calls.append(args)
        return len(calls) > 1

    monkeypatch.setattr(archive_snapshot, "save", save)
    server._save_archive_response_cache()
    assert server._ARCHIVE_RESPONSE_CACHE_LAST_SAVE_AT == 0
    server._save_archive_response_cache()
    assert server._ARCHIVE_RESPONSE_CACHE_LAST_SAVE_AT > 0
    server._save_archive_response_cache()
    assert len(calls) == 2


@pytest.mark.parametrize("previous_labels", [True, False])
def test_server_rebuilds_rows_cached_before_short_action_labels(tmp_path, monkeypatch, previous_labels):
    import server

    data = snapshot()
    data["schema_version"] = 11 if previous_labels else server._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION
    data["build_id"] = str(data["schema_version"])
    cache_file = tmp_path / "archive.json"
    cache_file.write_text(json.dumps(data))
    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE_FILE", cache_file)
    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE", {})
    monkeypatch.setattr(server, "_ARCHIVE_STATMAP_BY_SIG", {})
    server._read_archive_response_cache_file()
    if previous_labels:
        assert server._ARCHIVE_RESPONSE_CACHE == {}
        assert server._ARCHIVE_STATMAP_BY_SIG == {}
    else:
        assert server._ARCHIVE_RESPONSE_CACHE["base"]["conversations"] == [{"session_id": "session-a"}]
        assert "sig-a" in server._ARCHIVE_STATMAP_BY_SIG
