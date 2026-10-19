# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Unit tests for ccc_server/session_fts.index_health() (MEMO-FIX-24):
the memory-health snapshot `ccc doctor` surfaces for the session index and
its optional local-embeddings channel. All Ollama I/O is mocked -- these
tests never touch a real network service.
"""

import json
import os
from pathlib import Path

import pytest

import server
from ccc_server import session_fts


@pytest.fixture
def fts_env(tmp_path, monkeypatch):
    db_path = tmp_path / "session_fts.sqlite"
    projects_dir = tmp_path / "projects"
    codex_dir = tmp_path / "codex"
    projects_dir.mkdir(parents=True)
    codex_dir.mkdir(parents=True)

    monkeypatch.setenv("CCC_SESSION_FTS_DB", str(db_path))
    monkeypatch.setenv("CCC_PROJECTS_ROOT", str(projects_dir))
    monkeypatch.setenv("CCC_CODEX_SESSIONS_ROOT", str(codex_dir))
    monkeypatch.setenv("CCC_KIMI_SESSIONS_ROOT", str(tmp_path / "kimi-empty"))
    monkeypatch.setenv("CCC_GEMINI_TMP_ROOT", str(tmp_path / "gemini-empty"))
    monkeypatch.setenv("CCC_AGY_BRAIN_ROOTS", "")
    monkeypatch.setenv("CCC_CURSOR_PROJECTS_ROOT", str(tmp_path / "cursor-empty"))
    monkeypatch.setenv("CCC_HARVESTED_ROOT", str(tmp_path / "harvested-empty"))
    # Isolate the Hermes messages_fts channel (S9) -- see test_session_fts.py.
    monkeypatch.setattr(server, "HERMES_STATE_DB", tmp_path / "hermes" / "state.db")
    monkeypatch.setattr(server, "HERMES_PROFILES_DIR", tmp_path / "hermes" / "profiles")
    monkeypatch.setenv("CCC_SESSION_FTS_DAYS", "0")
    monkeypatch.setenv("CCC_SESSION_FTS_ALLOW_SCRATCH", "1")
    monkeypatch.setenv("CCC_SESSION_FTS_EMBED", "1")

    if hasattr(session_fts._tls, "conn") and session_fts._tls.conn:
        session_fts._tls.conn.close()
        session_fts._tls.conn = None
    session_fts._last_sync_ts = 0.0
    session_fts._ollama_state["ts"] = 0.0
    session_fts._ollama_state["ok"] = False
    session_fts._vec_cache["sids"] = []
    session_fts._vec_cache["vecs"] = []

    return {"db": db_path, "projects": projects_dir, "codex": codex_dir}


def _write_claude_jsonl(path: Path, turns: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(t) for t in turns]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_index_health_on_empty_index(fts_env, monkeypatch):
    """A fresh install (nothing indexed yet) must not crash doctor."""
    monkeypatch.setattr(session_fts, "_ollama_available", lambda: False)
    health = session_fts.index_health()
    assert health["sdoc_rows"] == 0
    assert health["semb_sids"] == 0
    assert health["semb_pending"] == 0
    assert health["ollama_reachable"] is False
    assert health["embed_model_present"] is None


def test_index_health_counts_reflect_a_real_sync(fts_env, monkeypatch):
    sid = "aaaaaaaa-0000-0000-0000-000000000001"
    _write_claude_jsonl(fts_env["projects"] / "repo" / f"{sid}.jsonl", [
        {"type": "user", "cwd": "/repo", "message": {"role": "user", "content": "investigate zephyrion signal drift"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "resolved"}]}},
    ])
    monkeypatch.setattr(session_fts, "_ollama_available", lambda: False)

    session_fts.search_sessions("zephyrion", force_refresh=True)

    health = session_fts.index_health()
    assert health["sdoc_rows"] == 1
    # Ollama unreachable -- the session is indexed but never got an embedding,
    # and is queued for the backfill loop to pick up once Ollama comes back.
    assert health["semb_sids"] == 0
    assert health["semb_pending"] == 1
    assert health["last_sync_ts"] is not None


def test_index_health_reports_embedding_coverage_when_ollama_is_up(fts_env, monkeypatch):
    sid = "bbbbbbbb-0000-0000-0000-000000000002"
    _write_claude_jsonl(fts_env["projects"] / "repo" / f"{sid}.jsonl", [
        {"type": "user", "cwd": "/repo", "message": {"role": "user", "content": "omega marker gamma delta"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "cleared"}]}},
    ])
    monkeypatch.setattr(session_fts, "_ollama_available", lambda: True)
    monkeypatch.setattr(session_fts, "_embed_texts", lambda texts, batch=32, timeout=None: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(session_fts, "_ollama_model_present", lambda: True)

    session_fts.search_sessions("omega", force_refresh=True)

    health = session_fts.index_health()
    assert health["sdoc_rows"] == 1
    assert health["semb_sids"] == 1
    assert health["semb_pending"] == 0
    assert health["ollama_reachable"] is True
    assert health["embed_model_present"] is True


def test_ollama_model_present_parses_tags_response(monkeypatch):
    class FakeResp:
        status = 200

        def read(self):
            return json.dumps({"models": [{"name": "nomic-embed-text:latest"}, {"name": "llama3.2:3b"}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(session_fts.urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    assert session_fts._ollama_model_present() is True


def test_ollama_model_present_false_when_model_missing(monkeypatch):
    class FakeResp:
        status = 200

        def read(self):
            return json.dumps({"models": [{"name": "llama3.2:3b"}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(session_fts.urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    assert session_fts._ollama_model_present() is False


def test_ollama_model_present_none_when_daemon_unreachable(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(session_fts.urllib.request, "urlopen", boom)
    assert session_fts._ollama_model_present() is None


def test_embed_model_dir_status_reachable(tmp_path, monkeypatch):
    models_dir = tmp_path / "ollama-models"
    models_dir.mkdir()
    monkeypatch.setenv("OLLAMA_MODELS", str(models_dir))
    status = session_fts._embed_model_dir_status()
    assert status["reachable"] is True
    assert status["on_volumes"] is False


def test_embed_model_dir_status_unreachable_when_unmounted(tmp_path, monkeypatch):
    """MEMO-FIX-24 (OPS-1251): a share that's unmounted at check time is the
    exact shape of the real incident -- the path simply fails to resolve."""
    missing = tmp_path / "not-mounted" / "models"
    monkeypatch.setenv("OLLAMA_MODELS", str(missing))
    status = session_fts._embed_model_dir_status()
    assert status["reachable"] is False
    assert status["resolved"] is None


def test_embed_model_dir_status_flags_network_volume(monkeypatch):
    """MEMO-FIX-24 (OPS-1251): the model dir living under /Volumes -- macOS's
    mount point for external/network shares -- is flagged even while it's
    still reachable, since an unmount can take it away at any moment."""
    monkeypatch.setattr(session_fts, "_embed_model_dir", lambda: Path("/Volumes/Lexar/ollama-models"))
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: Path("/Volumes/Lexar/ollama-models"))
    status = session_fts._embed_model_dir_status()
    assert status["reachable"] is True
    assert status["on_volumes"] is True
