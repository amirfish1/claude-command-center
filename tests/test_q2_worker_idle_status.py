"""Worker-idle evidence and Q2 integration regression coverage."""
import importlib
import json
import pathlib
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def server():
    mod = importlib.import_module("server")
    original = mod._wt_workers
    yield mod
    mod._wt_workers = original


def test_worker_idle_fields_use_watchtower_effective_evidence(server):
    calls = []

    def activity_evidence(worker):
        calls.append(worker["worker_id"])
        return {"effective": {"age_s": 3661.9, "source": "codex_rollout"}}

    server._wt_workers = SimpleNamespace(_activity_evidence=activity_evidence)
    row = server._wt_worker_idle_fields({"worker_id": "feedback-abc"})

    assert calls == ["feedback-abc"]
    assert row == {"idle_seconds": 3661, "idle_source": "codex_rollout"}


@pytest.mark.parametrize(
    "effective", [None, {}, {"age_s": -1}, {"age_s": float("nan")}, {"age_s": True}]
)
def test_worker_idle_fields_keep_unknown_evidence_neutral(server, effective):
    server._wt_workers = SimpleNamespace(
        _activity_evidence=lambda _worker: {"effective": effective}
    )
    assert server._wt_worker_idle_fields({"worker_id": "feedback-abc"}) == {
        "idle_seconds": None,
        "idle_source": "unknown",
    }


def test_worker_idle_fields_tolerate_older_watchtower(server):
    server._wt_workers = SimpleNamespace()
    assert server._wt_worker_idle_fields({"worker_id": "feedback-abc"}) == {
        "idle_seconds": None,
        "idle_source": "unknown",
    }


def test_read_workers_adds_only_bounded_idle_fields(server, tmp_path):
    workers_path = tmp_path / "workers.json"
    workers_path.write_text(
        json.dumps({
            "workers": [{"worker_id": "feedback-abc", "pid": 42, "queue": "FEEDBACK"}]
        }),
        encoding="utf-8",
    )
    server._wt_workers = SimpleNamespace(
        _activity_evidence=lambda _worker: {
            "effective": {
                "age_s": 12.8,
                "source": "watchtower_stdout",
                "path": "/private/leak",
            }
        }
    )
    with mock.patch.object(server, "_wt_workers_path", return_value=workers_path), \
         mock.patch.object(server.os, "kill", return_value=None):
        rows = server._wt_read_workers()

    assert rows[0]["idle_seconds"] == 12
    assert rows[0]["idle_source"] == "watchtower_stdout"
    assert "/private/leak" not in str(rows[0])


def test_q2_renders_idle_presentation_and_signature_bucket():
    source = (PROJECT_ROOT / "static" / "q2.js").read_text(encoding="utf-8")
    assert "Q2WorkerIdle.presentation(w.idle_seconds)" in source
    assert "Q2WorkerIdle.signatureBucket(w.idle_seconds)" in source
    assert "holding nothing" not in source
    assert "data-idle-severity" in source


def test_q2_idle_severity_styles_exist():
    source = (PROJECT_ROOT / "static" / "q2.css").read_text(encoding="utf-8")
    assert ".q2-dg-worker.is-idle-warning" in source
    assert ".q2-dg-worker.is-idle-stale" in source


def test_q2_worker_card_stops_spinning_the_warm_idle_tier():
    """A worker idle < 30m ('warm') used to fall through idleClass with no
    class at all, so it kept the plain is-live look -- a spinning ring
    identical to a worker actually attached to a ticket. It must now get its
    own class, and that class must swap the spin for a slow breathe rather
    than just recolouring a still-rotating ring."""
    js = (PROJECT_ROOT / "static" / "q2.js").read_text(encoding="utf-8")
    assert "idle.severity === 'warm' ? ' is-idle-warm'" in js

    css = (PROJECT_ROOT / "static" / "q2.css").read_text(encoding="utf-8")
    assert "q2-dg-sleepy" in css
    # Each idle tier's ring must be solid (no transparent gap -- that's what
    # makes the base .q2-dg-spin rule read as a rotating arc) and must join
    # the shared slow-breathe animation, not keep the fast rotation.
    for color in ("var(--green)", "var(--text-muted)", "var(--orange)", "var(--red)"):
        assert f"border-color: {color}; border-right-color: {color};" in css
    animated_tiers = css.split("animation: q2-dg-sleepy 2.6s ease-in-out infinite;", 1)[0]
    for tier in ("warm", "pending", "warning", "stale"):
        assert f".q2-dg-worker.is-idle-{tier} .q2-dg-spin" in animated_tiers


def test_rhs_status_strip_distinguishes_idle_from_working():
    """The RHS 'Auto: <queue>' status strip's per-worker dot used to render
    the same fast pulse for a worker that's idle (nothing claimed) as one
    genuinely attached to a ticket. It must now only use the fast pulse when
    a real claim is matched, and fall back to a slower, distinctly styled
    indicator otherwise."""
    js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    strip_fn = js.split("function _renderQueueStatusStrip(")[1].split("\n  function ", 1)[0]
    assert "const idleCls = on ? '' : ' is-idle';" in strip_fn
    assert 'class="fq-status-spin\' + idleCls' in strip_fn

    css = (PROJECT_ROOT / "static" / "app.css").read_text(encoding="utf-8")
    assert ".fq-status-spin.is-idle" in css
    idle_rule = css.split(".fq-status-spin.is-idle {", 1)[1].split("}", 1)[0]
    assert "var(--green" in idle_rule
    sleepy_duration = float(
        css.split(".fq-status-spin.is-idle {", 1)[1]
        .split("animation: fq-status-pulse-sleepy ", 1)[1]
        .split("s ", 1)[0]
    )
    working_duration = float(
        css.split(".fq-status-spin {", 1)[1]
        .split("animation: fq-status-pulse ", 1)[1]
        .split("s ", 1)[0]
    )
    assert sleepy_duration > working_duration * 2
