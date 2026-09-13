"""The worktree-dirty probe is a git subprocess per worktree path. Many
sessions share one path (the main clone); each carries its own last-event
timestamp. The soft TTL must mean "no session event since the last probe",
not "this session's timestamp equals the one that happened to be cached",
or the shared path is re-forked every 5 s forever."""
import server  # noqa: F401
from ccc_server import morning_launch as ml


def test_shared_path_is_not_reprobed_when_no_event_is_newer_than_the_probe(monkeypatch):
    clock = [1_000_000.0]
    monkeypatch.setattr(ml.time, "time", lambda: clock[0])
    probes = []
    monkeypatch.setattr(ml, "_worktree_is_dirty", lambda path: probes.append(path) or True)
    ml._WORKTREE_DIRTY_CACHE.clear()
    path = "/repo/main"

    assert ml._worktree_dirty_cached(path, 999_900.0) is True
    assert len(probes) == 1

    clock[0] += 6  # past the 5 s hard floor, inside the 30 s soft TTL
    # Another session on the same path with an older last event.
    assert ml._worktree_dirty_cached(path, 999_800.0) is True
    assert ml._worktree_dirty_cached(path, 999_900.0) is True
    assert len(probes) == 1

    # A session whose last event landed after the probe: re-probe once.
    assert ml._worktree_dirty_cached(path, 1_000_003.0) is True
    assert len(probes) == 2
    clock[0] += 6
    assert ml._worktree_dirty_cached(path, 1_000_003.0) is True
    assert ml._worktree_dirty_cached(path, 999_800.0) is True
    assert len(probes) == 2

    clock[0] += 31  # past the soft TTL: re-probe regardless
    assert ml._worktree_dirty_cached(path, 999_800.0) is True
    assert len(probes) == 3
    ml._WORKTREE_DIRTY_CACHE.clear()


def test_hard_floor_still_dedupes_inside_one_response(monkeypatch):
    clock = [2_000_000.0]
    monkeypatch.setattr(ml.time, "time", lambda: clock[0])
    probes = []
    monkeypatch.setattr(ml, "_worktree_is_dirty", lambda path: probes.append(path) or False)
    ml._WORKTREE_DIRTY_CACHE.clear()
    for ts in (2_000_001.0, 2_000_002.0, 2_000_003.0):
        assert ml._worktree_dirty_cached("/repo/wt", ts) is False
    assert len(probes) == 1
    ml._WORKTREE_DIRTY_CACHE.clear()
