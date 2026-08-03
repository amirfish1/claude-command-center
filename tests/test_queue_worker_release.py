"""Regression coverage for releasing a live queue worker from Q2."""
import importlib
import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _Queue:
    def __init__(self):
        self.item = {
            "ref": "DEMO-7", "project": "DEMO", "status": "in_progress",
            "claimed_by": "worker-7", "claimed_session_id": "session-7",
        }
        self.calls = []

    def list_items(self, **kwargs):
        self.calls.append(("list_items", kwargs))
        return [dict(self.item)]

    def release(self, ref, session_id=""):
        self.calls.append(("release", ref, session_id))
        self.item["status"] = "open"
        return dict(self.item)


class _Workers:
    def __init__(self): self.calls = []
    def request_stop(self, worker_id): self.calls.append(("request_stop", worker_id))
    def dispatch_after_enqueue(self, queue, ref): self.calls.append(("dispatch_after_enqueue", queue, ref))


@pytest.fixture
def server():
    mod = importlib.import_module("server")
    original = mod._q, mod._wt_workers, mod._WT_WORKERS_AVAILABLE, mod._wt_read_workers
    yield mod
    mod._q, mod._wt_workers, mod._WT_WORKERS_AVAILABLE, mod._wt_read_workers = original


def test_releasing_worker_requeues_ticket_stops_worker_and_dispatches(server):
    queue, workers = _Queue(), _Workers()
    server._q, server._wt_workers, server._WT_WORKERS_AVAILABLE = queue, workers, True
    server._wt_read_workers = lambda: [{"worker_id": "worker-7", "queue": "DEMO", "session_id": "session-7"}]

    result = server._release_queue_worker("worker-7")

    assert result["item"]["status"] == "open"
    assert queue.calls == [("list_items", {"fresh": True}), ("release", "DEMO-7", "worker-7")]
    assert workers.calls == [("request_stop", "worker-7"), ("dispatch_after_enqueue", "DEMO", "DEMO-7")]


def test_releasing_unknown_worker_is_rejected(server):
    server._wt_read_workers = lambda: []
    with pytest.raises(ValueError, match="not live"):
        server._release_queue_worker("gone-worker")


def test_q2_worker_card_exposes_release_action():
    q2_js = (PROJECT_ROOT / "static" / "q2.js").read_text(encoding="utf-8")
    assert "data-q2-release-worker" in q2_js
    assert "releaseWorker(" in q2_js
