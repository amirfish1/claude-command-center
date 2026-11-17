"""Concurrent sidebar consumers share one fresh title-marker scan."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

from ccc_server import session_graph as graph


def test_concurrent_cold_reads_scan_once_and_refresh_after_marker_change(tmp_path, monkeypatch):
    (tmp_path / 'session_autotitled').write_text('{"title":"First"}')
    (tmp_path / 'legacy_autotitled').write_text('123')
    monkeypatch.setattr(graph._core, 'SIDECAR_STATE_DIR', tmp_path)
    monkeypatch.setattr(graph, '_auto_titled_cache', {'key': None, 'data': {}})
    arrived = threading.Event()
    release = threading.Event()
    count_lock = threading.Lock()
    lock = threading.Lock()
    count = 0

    class ObservedLock:
        def __enter__(self):
            nonlocal count
            with count_lock:
                count += 1
                if count >= 8:
                    arrived.set()
            lock.acquire()

        def __exit__(self, *args):
            lock.release()

    monkeypatch.setattr(graph, '_auto_titled_lock', ObservedLock())
    original_listdir = graph.os.listdir
    scans = []

    def listdir(folder):
        if Path(folder) == tmp_path:
            scans.append(folder)
            assert release.wait(5)
        return original_listdir(folder)

    monkeypatch.setattr(graph.os, 'listdir', listdir)
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(graph._auto_titled_session_ids) for _ in range(8)]
        try:
            assert arrived.wait(5)
        finally:
            release.set()
        results = [job.result(timeout=5) for job in jobs]
    assert len(scans) == 1
    assert all(result == {'session': 'First', 'legacy': ''} for result in results)
    graph._auto_title_record('session', 'Second')
    assert graph._auto_titled_session_ids()['session'] == 'Second'
    assert len(scans) == 2


def test_unrelated_sidecar_changes_reuse_titles_but_refresh_changed_markers(tmp_path, monkeypatch):
    marker = tmp_path / 'session_autotitled'
    marker.write_text('{"title":"First"}')
    (tmp_path / 'removed_autotitled').write_text('123')
    monkeypatch.setattr(graph._core, 'SIDECAR_STATE_DIR', tmp_path)
    monkeypatch.setattr(graph, '_auto_titled_cache', {'key': None, 'data': {}})
    original_read = Path.read_text
    reads = []

    def read_text(filename, *args, **kwargs):
        if filename.parent == tmp_path:
            reads.append(filename.name)
        return original_read(filename, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read_text)
    assert graph._auto_titled_session_ids() == {'session': 'First', 'removed': ''}
    reads.clear()
    (tmp_path / 'unrelated_status').write_text('working')
    assert graph._auto_titled_session_ids() == {'session': 'First', 'removed': ''}
    assert reads == []

    # An atomic replacement must refresh even when its length is unchanged.
    replacement = tmp_path / 'replacement'
    replacement.write_text('{"title":"Other"}')
    replacement.replace(marker)
    (tmp_path / 'removed_autotitled').unlink()
    (tmp_path / 'new_autotitled').write_text('{"title":"New"}')
    assert graph._auto_titled_session_ids() == {'session': 'Other', 'new': 'New'}
    assert sorted(reads) == ['new_autotitled', 'session_autotitled']
    assert set(graph._auto_titled_cache['markers']) == {'session_autotitled', 'new_autotitled'}
