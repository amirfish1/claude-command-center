"""Disposable ticket reads preserve data and never publish partial output."""
import subprocess
import os
import json
from types import SimpleNamespace

import pytest

from ccc_server import wt_snapshot


def package(tmp_path, source):
    root = tmp_path / "watchtower"
    root.mkdir()
    (root / "__init__.py").write_text("")
    module = root / "queue.py"
    module.write_text(source)
    return SimpleNamespace(__file__=str(module))


def test_real_child_preserves_nested_ticket_fields_and_diagnostics(tmp_path):
    items = [{"ref": "TEST-1", "note": "שלום\nsecond line", "items_total": 3,
              "history": [{"event": "block", "question": "[Q1:test] Continue?"}],
              "resolution": {"summary": "Done", "commit": "abc"}}]
    queue = package(tmp_path, "def list_items():\n print('backend diagnostic')\n return " + repr(items))
    assert wt_snapshot.fetch(queue) == items


def test_empty_snapshot_is_success(tmp_path):
    assert wt_snapshot.fetch(package(tmp_path, "def list_items(): return []")) == []


def test_fetch_uses_separate_interpreter_without_dashboard_import(tmp_path):
    queue = package(tmp_path, "import os, sys\ndef list_items():\n"
                    " return [{'pid': os.getpid(), 'server_loaded': 'server' in sys.modules}]")
    [item] = wt_snapshot.fetch(queue)
    assert item["pid"] != os.getpid()
    assert item["server_loaded"] is False


def test_child_failure_does_not_return_partial_or_empty_success(tmp_path):
    queue = package(tmp_path, "def list_items(): raise RuntimeError('read failed')")
    with pytest.raises(subprocess.CalledProcessError):
        wt_snapshot.fetch(queue)


def test_timeout_reaps_child(tmp_path):
    queue = package(tmp_path, "import time\ndef list_items(): time.sleep(60)")
    with pytest.raises(subprocess.TimeoutExpired):
        wt_snapshot.fetch(queue, timeout=0.2)


@pytest.mark.parametrize("output", ['{"item": {"ref": "TEST-1"}}\n',
                                  '{"items_total": 1}\n',
                                  '{"items_total": 0}\n{}\n'])
def test_truncated_or_inconsistent_protocol_is_rejected(tmp_path, monkeypatch, output):
    queue = package(tmp_path, "")
    def run(args, *, stdout, **kwargs):
        stdout.write(output)
    monkeypatch.setattr(wt_snapshot.subprocess, "run", run)
    with pytest.raises(ValueError):
        wt_snapshot.fetch(queue)


def test_dashboard_retains_previous_snapshot_on_child_failure(tmp_path, monkeypatch):
    import server
    previous = [{"ref": "TEST-1", "title": "Existing title"}]
    monkeypatch.setattr(server, "_wt_items_memo", {"at": 0, "items": previous})
    monkeypatch.setattr(server, "_WT_ITEMS_SNAPSHOT_FILE", tmp_path / "absent.json")
    def fail(queue, **kwargs):
        raise subprocess.TimeoutExpired("snapshot", 90)
    monkeypatch.setattr(wt_snapshot, "fetch", fail)
    assert server._wt_list_items_refresh(0) is previous
    assert not server._WT_ITEMS_SNAPSHOT_FILE.exists()


def test_real_child_publishes_complete_display_snapshot(tmp_path):
    items = [{"ref": "TEST-1", "history": [{"text": "שלום\nsecond line"}]}]
    queue = package(tmp_path, "def list_items(): return " + repr(items))
    destination = tmp_path / "cache" / "snapshot.json"
    assert wt_snapshot.fetch(queue, snapshot_path=destination) == items
    assert json.loads(destination.read_text()) == {"items": items}
    assert list(destination.parent.iterdir()) == [destination]


def test_failed_child_keeps_existing_disk_snapshot(tmp_path):
    destination = tmp_path / "snapshot.json"
    previous = '{"items": [{"ref": "TEST-1"}]}'
    destination.write_text(previous)
    queue = package(tmp_path, "def list_items(): raise RuntimeError('read failed')")
    with pytest.raises(subprocess.CalledProcessError):
        wt_snapshot.fetch(queue, snapshot_path=destination)
    assert destination.read_text() == previous


def test_rejected_child_rows_cannot_publish_disk_snapshot(tmp_path):
    destination = tmp_path / "snapshot.json"
    destination.write_text("previous")
    queue = package(tmp_path, "def list_items(): return ['invalid ticket']")
    with pytest.raises(ValueError, match="invalid ticket snapshot item"):
        wt_snapshot.fetch(queue, snapshot_path=destination)
    assert destination.read_text() == "previous"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["snapshot.json", "watchtower"]


def test_unwritable_snapshot_does_not_discard_successful_fetch(tmp_path):
    destination = tmp_path / "not-a-directory"
    destination.write_text("existing")
    queue = package(tmp_path, "def list_items(): return [{'ref': 'TEST-1'}]")
    assert wt_snapshot.fetch(queue, snapshot_path=destination / "snapshot.json") == [{"ref": "TEST-1"}]
    assert destination.read_text() == "existing"


def test_publication_failure_cleans_temp_and_preserves_previous(tmp_path, monkeypatch):
    destination = tmp_path / "snapshot.json"
    destination.write_text("previous")
    def fail_replace(*args):
        raise OSError("write unavailable")
    monkeypatch.setattr(wt_snapshot.os, "replace", fail_replace)
    wt_snapshot._persist([{"ref": "TEST-1"}], destination)
    assert destination.read_text() == "previous"
    assert list(tmp_path.iterdir()) == [destination]
