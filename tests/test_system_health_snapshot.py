"""Resource snapshots stay bounded as the project archive grows."""
import importlib
import os

server = importlib.import_module("server")


def test_session_activity_stats_only_matching_transcripts(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    projects.mkdir()
    dirs = [projects / f"project-{i}" for i in range(60)]
    for directory in dirs:
        directory.mkdir()
    now = 1800000000.0
    rows = []
    registry = {}
    for i in range(8):
        sid = f"session-{i}"
        pid = 2000 + i
        transcript = dirs[i] / f"{sid}.jsonl"
        transcript.write_text("{}\n")
        os.utime(transcript, (now - 18000, now - 18000))
        registry[sid] = {"pid": pid}
        rows.append({"pid": pid, "ppid": 1, "rss_mb": 10, "cpu": 0,
                     "etime_min": 600, "tty": "?", "cmd": "claude -p"})
    # Resuming in another project must use the newest copy's activity.
    recent = dirs[-1] / "session-0.jsonl"
    recent.write_text("{}\n")
    os.utime(recent, (now - 60, now - 60))
    monkeypatch.setattr(server, "_SYS_PROJECTS_DIR", str(projects))
    monkeypatch.setattr(server, "_load_session_registry", lambda: registry)
    monkeypatch.setattr(server, "_load_session_name_overrides", lambda: {})
    monkeypatch.setattr(server, "_load_spawn_registry", lambda: [])
    monkeypatch.setattr(server, "_wt_live_worker_guard", lambda: ({2001}, set()))
    monkeypatch.setattr(server, "_sys_cwds", lambda pids: {})
    stats = []
    original = os.path.getmtime

    def counted(path):
        stats.append(path)
        return original(path)

    monkeypatch.setattr(os.path, "getmtime", counted)
    sessions = {row["session_id"]: row for row in server._sys_sessions(rows, now)}
    assert sessions["session-0"]["idle_min"] == 1
    assert not sessions["session-0"]["reapable"]
    assert sessions["session-1"]["wt_worker"]
    assert not sessions["session-1"]["reapable"]
    assert sessions["session-2"]["reapable"]
    assert len(stats) <= 9, "probe existing transcripts, not sessions x projects"
    os.utime(recent, (now, now))
    refreshed = {row["session_id"]: row for row in server._sys_sessions(rows, now)}
    assert refreshed["session-0"]["idle_min"] == 0


def test_snapshot_ttl_starts_when_the_build_finishes(monkeypatch):
    clock = [100.0]
    builds = []
    snapshot = {"ts": 0.0, "data": None}
    monkeypatch.setattr(server, "_system_health_snapshot", snapshot)
    monkeypatch.setattr(server.time, "time", lambda: clock[0])

    def build():
        builds.append(True)
        clock[0] += server._SYSTEM_HEALTH_TTL + 1
        return {"memory": {"used_mb": 100}, "ts": clock[0]}

    monkeypatch.setattr(server, "_build_system_health_uncached", build)
    first = server.build_system_health()
    assert server.build_system_health() is first
    assert len(builds) == 1
    # Destructive guards must still bypass the display cache.
    server.build_system_health(force=True)
    assert len(builds) == 2
