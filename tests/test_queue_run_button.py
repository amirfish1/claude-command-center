"""The Queue panel's single ▶ control (2026-07-26 GitHub-queue design, Part 3).

▶ means one thing: run this ticket. It sets ``run_requested`` on the ticket
through WatchTower's own API and lets WatchTower's reconciler do the spawning,
so N presses run serially inside ``desired_workers`` instead of spawning N
unbudgeted workers, and the mark survives a reload. Pressing ▶ again while the
ticket is still only queued cancels the request.

These tests pin the parts CCC owns:

  * the endpoint flips the flag through the queue module (never CCC writing
    WatchTower's JSON) and verifies it stuck, in both directions;
  * it nudges exactly like ``dispatch_after_enqueue`` and spawns nothing
    itself, which is what makes presses serial;
  * behaviour does not branch on the backend, so a GitHub-backed and a
    file-backed queue behave identically;
  * the row renders the store's truth (open → queued to run → running →
    needs input / closed) with no client-side "starting worker" latch — the
    bug that hid the working button forever once drain was off.

No network: the queue module is a stub shaped like ``watchtower.queue``.
"""
import importlib
import inspect
import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _StubQueue:
    """Stand-in for ``watchtower.queue`` carrying the ▶ primitives.

    ``records_flag=False`` models an older WatchTower: ``mark_runnable`` still
    does something (it adds the legacy whitelist label) but never stores
    ``run_requested``.

    ``cancel_name=None`` models a WatchTower with no dedicated cancel
    primitive, so the helper has to fall back to a field patch.
    """

    def __init__(self, item, *, records_flag=True, cancel_name="cancel_run_request"):
        self.item = dict(item)
        self.records_flag = records_flag
        self.calls = []
        if cancel_name:
            setattr(self, cancel_name, self._cancel)

    def mark_runnable(self, ref):
        self.calls.append(("mark_runnable", ref))
        if self.records_flag:
            self.item["run_requested"] = True
        return dict(self.item)

    def _cancel(self, ref):
        self.calls.append(("cancel", ref))
        if self.records_flag:
            self.item["run_requested"] = False
        return dict(self.item)

    def update(self, ref, **fields):
        self.calls.append(("update", ref, fields))
        if self.records_flag and "run_requested" in fields:
            self.item["run_requested"] = bool(fields["run_requested"])
        return dict(self.item)

    def get(self, ref):
        return dict(self.item)


_FILE_TICKET = {
    "ref": "DEMO-7", "project": "DEMO", "status": "open",
    "note": "file-backed ticket", "run_requested": False,
}
# Same shape as the GitHub backend's output: the flag is a label there, but the
# backend normalises it to the same boolean, so CCC cannot tell them apart.
_GH_TICKET = {
    "ref": "DEMO-GH-42", "project": "DEMO-GH", "status": "open",
    "note": "github-backed ticket", "github_repo": "example/demo",
    "run_requested": False,
}


@pytest.fixture
def server():
    mod = importlib.import_module("server")
    original = mod._q
    yield mod
    mod._q = original


def _run_block(server):
    """The do_POST branch that serves POST /api/ux-fixes/run."""
    src = inspect.getsource(server.CommandCenterHandler.do_POST)
    start = src.index('if path == "/api/ux-fixes/run":')
    return src[start:src.index('if path == "/api/ux-fixes/run-once":', start)]


def _app_js():
    return (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")


def _queue_render_js(app_js):
    start = app_js.index("function _renderQueuePanel(options)")
    return app_js[start:app_js.index("// Jump the conversation pane", start)]


def _run_handler_js(app_js):
    start = app_js.index("const runBtn = ev.target")
    return app_js[start:app_js.index("const bumpBtn = ev.target", start)]


# --- server: flipping the flag through WatchTower ---------------------------

@pytest.mark.parametrize("ticket", [_FILE_TICKET, _GH_TICKET],
                         ids=["file-backed", "github-backed"])
def test_play_sets_run_requested_through_watchtower(server, ticket):
    """Identical on both backends — CCC never learns which one it is."""
    stub = _StubQueue(ticket)
    server._q = stub

    item, warning = server._uxq_set_run_requested(ticket["ref"], True)

    assert stub.calls == [("mark_runnable", ticket["ref"])]
    assert item["run_requested"] is True
    assert warning == ""


@pytest.mark.parametrize("ticket", [_FILE_TICKET, _GH_TICKET],
                         ids=["file-backed", "github-backed"])
def test_second_press_cancels_the_queued_run(server, ticket):
    stub = _StubQueue(dict(ticket, run_requested=True))
    server._q = stub

    item, warning = server._uxq_set_run_requested(ticket["ref"], False)

    assert stub.calls == [("cancel", ticket["ref"])]
    assert item["run_requested"] is False
    assert warning == ""


def test_cancel_falls_back_to_a_field_patch_not_a_store_write(server):
    """No dedicated primitive: patch the field via the queue module anyway."""
    stub = _StubQueue(dict(_FILE_TICKET, run_requested=True), cancel_name=None)
    server._q = stub

    item, _ = server._uxq_set_run_requested("DEMO-7", False)

    assert stub.calls == [("update", "DEMO-7", {"run_requested": False})]
    assert item["run_requested"] is False


def test_cancel_that_did_not_stick_is_an_error(server):
    """A ticket still queued to run must not report a successful cancel."""
    stub = _StubQueue(dict(_FILE_TICKET, run_requested=True), records_flag=False)
    server._q = stub

    with pytest.raises(ValueError) as exc:
        server._uxq_set_run_requested("DEMO-7", False)
    assert "still queued to run" in str(exc.value)


def test_play_on_an_engine_that_cannot_store_the_flag_warns(server):
    """Marking has a real side effect on older WatchTowers, so this is not a
    failure — but it must not claim the ticket is queued either."""
    stub = _StubQueue(_FILE_TICKET, records_flag=False)
    server._q = stub

    item, warning = server._uxq_set_run_requested("DEMO-7", True)

    assert not item.get("run_requested")
    assert "update WatchTower" in warning


def test_helper_never_touches_watchtowers_store_directly(server):
    src = inspect.getsource(server._uxq_set_run_requested)
    assert "open(" not in src and "json.dump" not in src
    assert "github" not in src.lower(), "the backend must stay invisible here"


# --- server: the endpoint ---------------------------------------------------

def test_endpoint_toggles_and_reports_the_stored_state(server):
    block = _run_block(server)
    assert 'cancel = bool(payload.get("cancel"))' in block
    assert "_uxq_set_run_requested(ref, not cancel)" in block
    assert '"queued": bool(item.get("run_requested"))' in block, (
        "the response must carry the store's truth, not the request's intent")
    assert '"warning": warning' in block


def test_endpoint_nudges_but_never_spawns(server):
    """The reconciler owns concurrency: three presses must not become three
    workers, which is exactly what spawning from here would do."""
    block = _run_block(server)
    assert "dispatch_after_enqueue" in block
    assert "if not cancel and" in block, "cancelling must not nudge the queue"
    code = "\n".join(l for l in block.splitlines() if not l.lstrip().startswith("#"))
    assert "spawn" not in code


# --- static: one button, real states ---------------------------------------

def test_one_play_button_replaces_the_pair():
    app_js = _app_js()
    queue_js = _queue_render_js(app_js)
    assert queue_js.count("fq-status fq-status-action fq-run") == 1
    assert "fq-run-once" not in app_js
    assert "fq-run-once" not in (PROJECT_ROOT / "static" / "app.css").read_text(encoding="utf-8")


def test_queued_to_run_is_rendered_from_the_ticket():
    queue_js = _queue_render_js(_app_js())
    assert "const queuedToRun = status === 'open' && !!it.run_requested;" in queue_js
    assert "'Queued to run - click to cancel'" in queue_js
    assert "data-run-cancel=" in queue_js
    assert "is-queued-run" in queue_js


def test_no_latching_spinner_and_no_false_running_toast():
    """Both bugs the design names: a pending-run marker that cleared only when
    the ticket left `open` (so with drain off it latched forever and covered
    the working button), and a toast asserting "Running" when nothing ran."""
    app_js = _app_js()
    assert "_uxqPendingRunRefs" not in app_js
    assert "showOpToast('Running ' + ref)" not in app_js

    handler = _run_handler_js(app_js)
    assert "} finally {" in handler, "the busy marker must clear on every path"
    assert "_uxqRunBusyRefs.delete(ref)" in handler
    assert "'Queued ' + ref + ' to run'" in handler
    assert "'Cancelled the queued run for '" in handler
    assert "JSON.stringify({ ref, cancel })" in handler


def test_github_board_polls_within_the_five_second_budget():
    """Part 2, CCC half: a new issue must be on the board within 5s."""
    qe = importlib.import_module("ccc_server.queue_events")
    assert qe._GH_POLL_INTERVAL_S == 5.0
