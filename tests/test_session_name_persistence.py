"""Concurrent renames must preserve peers' names and always expose valid JSON."""
import importlib
import json
import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _rename_in_process(directory, sid):
    os.environ["HOME"] = directory
    importlib.import_module("server")
    graph = importlib.import_module("ccc_server.session_graph")
    names = Path(directory) / "session-names.json"

    def read_names():
        return json.loads(names.read_text()) if names.exists() else {}

    def atomic_write(path, data):
        temp = path.with_suffix("." + sid + ".tmp")
        temp.write_text(json.dumps(data))
        temp.replace(path)

    core = SimpleNamespace(LOG_VIEWER_STATE_DIR=names.parent, SESSION_NAMES_FILE=names,
                           _load_session_name_overrides=read_names,
                           _truncate_session_name=str, _atomic_write_json=atomic_write)
    with mock.patch.object(graph, "_core", core):
        for i in range(10):
            graph._save_session_name_override(sid, f"Name {sid} {i}")


def test_parallel_renames_preserve_every_name(tmp_path):
    context = multiprocessing.get_context("spawn")
    workers = [context.Process(target=_rename_in_process, args=(str(tmp_path), str(i)))
               for i in range(6)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)
        assert worker.exitcode == 0
    names = json.loads((tmp_path / "session-names.json").read_text())
    assert names == {str(i): f"Name {i} 9" for i in range(6)}
