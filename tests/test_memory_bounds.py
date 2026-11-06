"""Memory bounds a small laptop depends on (CLAUDE.md "Performance gates").

Every cache here once had only an entry cap, which bounds nothing when one
entry is a whole transcript or a 13 MB list body. If one of these fails, a
cache lost its byte budget: restore it, don't raise the number.
"""
import importlib
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from ccc_server import memory_release

server = importlib.import_module("server")
MB = 1024 * 1024
BYTE_BUDGET_CEILING = 64 * MB


def test_default_budgets_stay_small():
    for budget in (server._ARCHIVE_LIST_BODY_CACHE_BYTES, server._CONV_BYTES_CACHE_BYTES,
                   server._CONV_PARSE_CACHE_BYTES):
        assert budget <= BYTE_BUDGET_CEILING


def test_conversation_bytes_cache_holds_its_budget(monkeypatch):
    cache = server._CONV_BYTES_CACHE
    cache.clear()
    monkeypatch.setattr(server, "_conv_parse_jsonl_mtime", lambda *a, **k: (1, 1))
    monkeypatch.setattr(server, "_conv_overlay_fingerprint", lambda sid: "fp")
    body = b"x" * (2 * MB)
    for i in range(200):  # 400 MB offered
        server._conv_response_bytes_put("sid-%d" % i, 0, body, body[:1000])
    assert cache.bytes <= server._CONV_BYTES_CACHE_BYTES
    assert len(cache) > 0
    assert server._conv_response_bytes_get("sid-199", 0) is not None  # newest survives
    cache.clear()


def test_conversation_parse_cache_weighs_big_transcripts(monkeypatch):
    cache = server._CONV_PARSE_CACHE
    cache.clear()
    size = 5 * MB
    monkeypatch.setattr(server, "_conv_parse_jsonl_mtime", lambda *a, **k: (1, size))
    for i in range(512):  # the old entry cap, at a size real transcripts reach
        server._conv_parse_cache_put("sid-%d" % i, 0, None, {"events": [{}], "last_line": 1})
    assert len(cache) * size * server._CONV_PARSE_BYTES_PER_SOURCE_BYTE <= server._CONV_PARSE_CACHE_BYTES
    cache.clear()


def test_list_body_cache_drops_stale_snapshots_by_bytes(monkeypatch):
    cache = server._ARCHIVE_LIST_BODY_CACHE
    cache.clear()
    monkeypatch.setattr(server, "_archive_list_disk_save", lambda *a, **k: None)
    payload = {"rows": ["x" * 1024] * (4 * 1024)}  # about 4 MB of JSON, gzips to little
    for version in range(1, 60):  # each new snapshot is a new key
        server._archive_list_body_get_or_build("k", "all", False, lambda: payload, version)
    assert 0 < cache.bytes <= server._ARCHIVE_LIST_BODY_CACHE_BYTES
    assert len(cache) < 59
    raw, _, _ = server._archive_list_body_get_or_build(
        "k", "all", False, lambda: (_ for _ in ()).throw(AssertionError("rebuilt")), 59)
    assert raw  # the newest body is still served from memory
    cache.clear()


def test_stale_refresh_results_are_swept_and_fresh_ones_kept(tmp_path):
    prefix = server._ARCHIVE_REFRESH_RESULT_PREFIX
    stale, fresh = tmp_path / (prefix + "old.json"), tmp_path / (prefix + "new.json")
    other = tmp_path / "unrelated.json"
    for path in (stale, fresh, other):
        path.write_text("{}")
    long_ago = time.time() - 86400
    os.utime(stale, (long_ago, long_ago))
    os.utime(other, (long_ago, long_ago))
    assert server._sweep_stale_archive_refresh_results(root=str(tmp_path)) == 1
    assert not stale.exists() and fresh.exists() and other.exists()


def test_release_runs_on_this_platform():
    assert memory_release.release_freed_memory() in (True, False)


def test_helpers_start_the_memory_release():
    root = Path(server.__file__).resolve().parent
    for rel in ("ccc_server/bg_helper.py", "ccc_server/conv_reader.py", "ccc_worker.py"):
        assert "memory_release.start()" in (root / rel).read_text()


def test_freed_heap_goes_back_to_the_os():
    """Run apart: pytest's own heap would hide the drop."""
    if not sys.platform.startswith("linux"):
        return
    root = Path(server.__file__).resolve().parent
    code = textwrap.dedent("""
        import json, sys
        sys.path.insert(0, sys.argv[1])
        from ccc_server import memory_release
        memory_release.cap_allocator_arenas()
        def rss():
            with open("/proc/self/status") as f:
                return next(int(l.split()[1]) // 1024 for l in f if l.startswith("VmRSS"))
        base = rss()
        keep, junk = [], [bytes(4000 + i % 64) for i in range(100000)]
        keep.extend(junk[::1000])
        peak = rss()
        del junk
        memory_release.release_freed_memory()
        print(json.dumps({"base": base, "peak": peak, "after": rss()}))
    """)
    out = subprocess.run([sys.executable, "-c", code, str(root)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    import json
    got = json.loads(out.stdout)
    assert got["peak"] - got["base"] > 200  # the churn really allocated
    assert got["after"] < got["base"] + (got["peak"] - got["base"]) * 0.25


def test_throughput_turn_cache_holds_its_budget(monkeypatch):
    from ccc_server import usage_stats

    budget = 4 * MB
    monkeypatch.setattr(usage_stats, "_THROUGHPUT_TURN_CACHE",
                        usage_stats.ByteBudgetCache(budget, 100_000))
    turns = [{"tokens": 1}] * 512
    for i in range(50):
        usage_stats._throughput_cache_put(f"/t/{i}", {"mtime": 1, "size": 1, "turns": turns})
    cache = usage_stats._THROUGHPUT_TURN_CACHE
    assert cache.bytes <= budget
    assert len(cache) == budget // usage_stats._throughput_turns_weight(turns)
    assert usage_stats._throughput_turns_weight([]) == 0
    assert cache.get("/t/49")["turns"] is turns


def test_throughput_default_budget_stays_small():
    from ccc_server import usage_stats

    assert usage_stats._THROUGHPUT_TURN_CACHE.max_bytes <= BYTE_BUDGET_CEILING


def test_boot_runs_server_from_bytecode_and_keeps_script_dispatch(tmp_path):
    root = Path(server.__file__).resolve().parent
    done = subprocess.run(
        [sys.executable, str(root / "ccc_server" / "boot.py"), str(root / "server.py"),
         "--archive-refresh-worker"],
        env={**os.environ, "HOME": str(tmp_path)}, capture_output=True, text=True, timeout=120,
    )
    assert done.returncode == 2, done.stderr
    assert "usage: server.py --archive-refresh-worker" in done.stderr


def test_every_server_relaunch_goes_through_boot():
    argv = server._server_launch_argv()
    assert argv[1].endswith("boot.py")
    # Process discovery and uninstall match this name on the command line.
    assert os.path.basename(argv[2]) == "server.py"
