"""_antigravity_cli_log_paths runs inside every sessions-snapshot refresh
(gemini and antigravity scans) and globbed every known repo's log dir, one
of which holds 21,000 entries, several times per call. Glob a dir once per
directory version and skip duplicate roots."""
import os

import server  # noqa: F401
from ccc_server import engines


def _bump_dir(path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


def test_log_dirs_are_globbed_once_per_directory_version(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "spawn-antigravity-1.log.agy.log").write_text("x")
    cli_home = tmp_path / "cli"
    (cli_home / "log").mkdir(parents=True)
    (cli_home / "log" / "a.log").write_text("x")

    monkeypatch.setattr(engines._core, "repo_log_dir", lambda root: log_dir)
    monkeypatch.setattr(engines._core, "_load_recent_repos", lambda: ["/r/one", "/r/two"])
    monkeypatch.setattr(engines._core, "_load_custom_repos", lambda: ["/r/one"])
    monkeypatch.setattr(engines._core, "ANTIGRAVITY_CLI_HOME", cli_home)
    engines._ANTIGRAVITY_LOG_DIR_CACHE.clear()
    globbed = []
    real = engines._antigravity_glob_dir_uncached

    def counting(directory, patterns):
        globbed.append(str(directory))
        return real(directory, patterns)

    monkeypatch.setattr(engines, "_antigravity_glob_dir_uncached", counting)

    first = engines._antigravity_cli_log_paths()
    assert sorted(p.name for p in first) == ["a.log", "spawn-antigravity-1.log.agy.log"]
    # Three roots map onto one log dir plus the CLI log dir: two globs, not six.
    assert sorted(globbed) == sorted([str(log_dir), str(cli_home / "log")])

    second = engines._antigravity_cli_log_paths()
    assert [str(p) for p in second] == [str(p) for p in first]
    assert len(globbed) == 2

    (log_dir / "resume-antigravity-2.log.agy.log").write_text("x")
    _bump_dir(log_dir)
    third = engines._antigravity_cli_log_paths()
    assert len(third) == 3
    assert globbed.count(str(log_dir)) == 2
    engines._ANTIGRAVITY_LOG_DIR_CACHE.clear()
