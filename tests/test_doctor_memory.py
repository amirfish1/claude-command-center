# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Tests for server.build_memory_doctor() (MEMO-FIX-24): the memory-health
section of `ccc doctor` -- session index / embeddings coverage, ship-graph
freshness, and decision-extraction last-run. A real incident (Ollama's model
directory sat on an unmounted SMB share for hours; only 1/2779 sessions ever
got embedded) went undetected because nothing polled this. These tests mock
the three underlying modules so they never touch real Ollama/git/sqlite
state -- see test_session_fts_health.py and test_ship_graph.py for coverage
of the underlying health functions themselves.
"""

from __future__ import annotations

import importlib
from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _fresh_server():
    for name in ("server", "morning", "morning_store"):
        sys.modules.pop(name, None)
    return importlib.import_module("server")


HEALTHY_INDEX = {
    "sdoc_rows": 100,
    "semb_sids": 98,
    "semb_pending": 2,
    "last_sync_ts": 1234.0,
    "ollama_reachable": True,
    "embed_model_present": True,
    "embed_model_dir": {"path": "~/.ollama/models", "resolved": "/Users/x/.ollama/models-local", "reachable": True, "on_volumes": False},
}
HEALTHY_GRAPH = {"transcripts_rows": 50, "commits_rows": 20, "last_sync_ts": 1234.0, "indexing": False}


_RECENT = object()


def _recent_iso():
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _patch_memory_modules(monkeypatch, *, index=None, graph=None, last_run_at=_RECENT, cfg=None):
    from ccc_server import session_fts, ship_graph, decision_extraction

    if last_run_at is _RECENT:
        # Relative to now: a hardcoded date ages past the "overdue" threshold
        # and flips the healthy case to warn.
        last_run_at = _recent_iso()

    monkeypatch.setattr(session_fts, "index_health", lambda: index if index is not None else dict(HEALTHY_INDEX))
    monkeypatch.setattr(ship_graph, "graph_health", lambda: graph if graph is not None else dict(HEALTHY_GRAPH))
    monkeypatch.setattr(decision_extraction, "last_run_at", lambda: last_run_at)
    monkeypatch.setattr(decision_extraction, "load_config", lambda: cfg if cfg is not None else {"enabled": True, "run_every_s": 20 * 3600})


def test_memory_doctor_ok_when_everything_healthy(monkeypatch):
    server = _fresh_server()
    recent = _recent_iso()
    _patch_memory_modules(monkeypatch, last_run_at=recent)

    report = server.build_memory_doctor()

    assert report["status"] == "ok"
    assert report["warnings"] == []
    assert report["session_index"]["embed_coverage_pct"] == 98.0
    assert report["decision_extraction"]["last_run_at"] == recent


def test_memory_doctor_included_in_ccc_doctor(monkeypatch):
    server = _fresh_server()
    expected = {"status": "ok", "warnings": []}
    monkeypatch.setattr(server, "build_memory_doctor", lambda: expected)

    report = server.build_ccc_doctor()

    assert report["memory"] == expected


def test_memory_doctor_warns_when_ollama_unreachable(monkeypatch):
    server = _fresh_server()
    index = dict(HEALTHY_INDEX)
    index["ollama_reachable"] = False
    index["embed_model_present"] = None
    _patch_memory_modules(monkeypatch, index=index)

    report = server.build_memory_doctor()

    assert report["status"] == "warn"
    assert any("Ollama unreachable" in w for w in report["warnings"])


def test_memory_doctor_warns_on_low_embedding_coverage(monkeypatch):
    """MEMO-FIX-24's actual incident: 1/2779 sessions embedded, silently."""
    server = _fresh_server()
    index = dict(HEALTHY_INDEX)
    index["sdoc_rows"] = 2779
    index["semb_sids"] = 1
    index["semb_pending"] = 0
    _patch_memory_modules(monkeypatch, index=index)

    report = server.build_memory_doctor()

    assert report["status"] == "warn"
    assert any("0.0%" in w or "only" in w for w in report["warnings"])


def test_memory_doctor_warns_when_model_dir_unreachable(monkeypatch):
    """MEMO-FIX-24 (OPS-1251): the unmounted-share shape."""
    server = _fresh_server()
    index = dict(HEALTHY_INDEX)
    index["embed_model_dir"] = {"path": "~/.ollama/models", "resolved": None, "reachable": False, "on_volumes": None}
    _patch_memory_modules(monkeypatch, index=index)

    report = server.build_memory_doctor()

    assert report["status"] == "warn"
    assert any("unreachable" in w for w in report["warnings"])


def test_memory_doctor_warns_when_model_dir_on_network_volume(monkeypatch):
    server = _fresh_server()
    index = dict(HEALTHY_INDEX)
    index["embed_model_dir"] = {"path": "~/.ollama/models", "resolved": "/Volumes/Lexar/models", "reachable": True, "on_volumes": True}
    _patch_memory_modules(monkeypatch, index=index)

    report = server.build_memory_doctor()

    assert report["status"] == "warn"
    assert any("mounted volume" in w for w in report["warnings"])


def test_memory_doctor_warns_when_ship_graph_empty(monkeypatch):
    server = _fresh_server()
    graph = {"transcripts_rows": 0, "commits_rows": 0, "last_sync_ts": None, "indexing": False}
    _patch_memory_modules(monkeypatch, graph=graph)

    report = server.build_memory_doctor()

    assert report["status"] == "warn"
    assert any("ship graph is empty" in w for w in report["warnings"])


def test_memory_doctor_does_not_warn_on_empty_graph_while_indexing(monkeypatch):
    server = _fresh_server()
    graph = {"transcripts_rows": 0, "commits_rows": 0, "last_sync_ts": None, "indexing": True}
    _patch_memory_modules(monkeypatch, graph=graph)

    report = server.build_memory_doctor()

    assert not any("ship graph is empty" in w for w in report["warnings"])


def test_memory_doctor_warns_when_decisions_never_ran(monkeypatch):
    server = _fresh_server()
    _patch_memory_modules(monkeypatch, last_run_at=None)

    report = server.build_memory_doctor()

    assert report["status"] == "warn"
    assert any("never run" in w for w in report["warnings"])


def test_memory_doctor_warns_when_decisions_stale(monkeypatch):
    server = _fresh_server()
    _patch_memory_modules(monkeypatch, last_run_at="2020-01-01T00:00:00Z")

    report = server.build_memory_doctor()

    assert report["status"] == "warn"
    assert any("last ran" in w for w in report["warnings"])


def test_memory_doctor_no_decision_warning_when_extraction_disabled(monkeypatch):
    server = _fresh_server()
    _patch_memory_modules(monkeypatch, last_run_at=_RECENT, cfg={"enabled": False})

    report = server.build_memory_doctor()

    assert not any("decision extraction" in w for w in report["warnings"])
