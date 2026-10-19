# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Unit tests for the optional local-embeddings (Ollama) channel in
ccc_server/session_fts.py: RRF fusion, graceful degradation, and incremental
(mtime, size) embedding. All Ollama I/O is mocked -- these tests never touch
a real network service, whether or not the dev box happens to run Ollama.
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


def test_rrf_fusion_surfaces_vector_only_match(fts_env, monkeypatch):
    """A session with zero FTS lexical overlap can still surface via the
    (mocked) embeddings channel, fused in by RRF -- the whole point of P2."""
    sid_fts = "aaaaaaaa-0000-0000-0000-000000000001"
    sid_vec = "bbbbbbbb-0000-0000-0000-000000000002"

    _write_claude_jsonl(fts_env["projects"] / "repo" / f"{sid_fts}.jsonl", [
        {"type": "user", "cwd": "/repo", "message": {"role": "user", "content": "investigate zephyrion signal drift"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "zephyrion resolved"}]}},
    ])
    _write_claude_jsonl(fts_env["projects"] / "repo" / f"{sid_vec}.jsonl", [
        {"type": "user", "cwd": "/repo", "message": {"role": "user", "content": "omega marker gamma delta printer jam"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "cleared the jam"}]}},
    ])

    monkeypatch.setattr(session_fts, "_ollama_available", lambda: True)

    def fake_embed(texts, batch=32, timeout=None):
        out = []
        for t in texts:
            if t == "search_query: zephyrion signal drift" or "omega marker" in t:
                out.append([0.0, 1.0, 0.0])
            else:
                out.append([1.0, 0.0, 0.0])
        return out

    monkeypatch.setattr(session_fts, "_embed_texts", fake_embed)

    results = session_fts.search_sessions("zephyrion signal drift", force_refresh=True)
    sids = [r["session_id"] for r in results]

    assert sid_fts in sids, "lexical match must still be found"
    assert sid_vec in sids, "vector-only match must be fused in via RRF"


def test_rrf_weights_keep_fts_channel_authoritative_over_vector_noise():
    """MEMORY-19: at the old *equal*-weight RRF (session_fts._rrf had no
    `weights` param at all), a session with zero real relevance but merely
    present in the vector channel's top-k could accumulate enough combined
    score to outrank a session that was the single best -- and only --
    FTS/keyword match. Measured on a real query
    (~/dev/scratch/memory-19-benchmark): a session ranked #1 by FTS alone,
    and completely absent from the vector channel's own top-50, fell to
    rank 11 after unweighted RRF fusion, because several other sessions each
    got a small vector-channel credit the true answer got none of. This is
    that exact shape, reproduced numerically: `best` is FTS-rank-1 and
    vector-absent; four unrelated sessions rank 2-5 in FTS but each also
    appear somewhere in the vector channel, so their summed equal-weight RRF
    score passes `best`'s FTS-only score. Down-weighting the vector channel
    (session_fts._VECTOR_RRF_WEIGHT) must restore `best` to rank 1."""
    # `rival` is a mediocre FTS match (rank 15 of 15) but happens to be the
    # single best hit in the vector channel; `best` is the clean FTS rank-1
    # match and gets zero vector-channel credit -- same shape as the real
    # measured case (a top-1 FTS session absent from the vector channel's
    # own top-k).
    fts_sids = ["best"] + [f"pad{i}" for i in range(13)] + ["rival"]
    vector_sids = ["rival"]
    assert fts_sids.index("rival") + 1 == 15
    assert fts_sids.index("best") + 1 == 1

    equal = session_fts._rrf([fts_sids, vector_sids])
    assert equal[0] == "rival", (
        "sanity check: unweighted RRF should reproduce the measured bug "
        f"(mediocre-FTS-but-vector-rank-1 'rival' outranks 'best'), got {equal}"
    )

    weighted = session_fts._rrf(
        [fts_sids, vector_sids], weights=[1.0, session_fts._VECTOR_RRF_WEIGHT],
    )
    assert weighted[0] == "best", (
        f"down-weighted vector channel must keep the clean FTS top match on top, got {weighted}"
    )


def test_search_degrades_silently_when_ollama_unavailable(fts_env, monkeypatch):
    """No Ollama daemon (the default): identical FTS-only behavior, no error."""
    sid = "cccccccc-0000-0000-0000-000000000003"
    _write_claude_jsonl(fts_env["projects"] / "repo" / f"{sid}.jsonl", [
        {"type": "user", "cwd": "/repo", "message": {"role": "user", "content": "debugging flux capacitor overheating"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "fixed"}]}},
    ])

    monkeypatch.setattr(session_fts, "_ollama_available", lambda: False)

    def boom(*a, **kw):
        raise AssertionError("must not call Ollama when unavailable")

    monkeypatch.setattr(session_fts, "_embed_texts", boom)

    results = session_fts.search_sessions("flux capacitor overheating", force_refresh=True)
    assert len(results) == 1
    assert results[0]["session_id"] == sid


def test_search_degrades_silently_when_embed_call_fails(fts_env, monkeypatch):
    """Ollama reachable but the embed call itself errors (model missing, etc.)."""
    sid = "dddddddd-0000-0000-0000-000000000004"
    _write_claude_jsonl(fts_env["projects"] / "repo" / f"{sid}.jsonl", [
        {"type": "user", "cwd": "/repo", "message": {"role": "user", "content": "narwhal telemetry parsing bug"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "parsed"}]}},
    ])

    monkeypatch.setattr(session_fts, "_ollama_available", lambda: True)
    monkeypatch.setattr(session_fts, "_embed_texts", lambda texts, batch=32, timeout=None: None)

    results = session_fts.search_sessions("narwhal telemetry parsing", force_refresh=True)
    assert len(results) == 1
    assert results[0]["session_id"] == sid


def test_embeddings_incremental_by_mtime_size(fts_env, monkeypatch):
    """Only new/changed sessions get (re-)embedded; unchanged ones are skipped,
    mirroring the FTS index's own (mtime, size) incremental cache."""
    monkeypatch.setattr(session_fts, "_ollama_available", lambda: True)

    index_calls = []  # batches of document (session) chunks -- the incremental path

    def counting_embed(texts, batch=32, timeout=None):
        if texts and texts[0].startswith("search_document: "):
            index_calls.append(len(texts))
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(session_fts, "_embed_texts", counting_embed)

    for i in range(3):
        sid = f"session-{i:04d}"
        _write_claude_jsonl(fts_env["projects"] / "repo" / f"{sid}.jsonl", [
            {"type": "user", "cwd": "/repo", "message": {"role": "user", "content": f"task {i} kernel work"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}},
        ])

    session_fts.search_sessions("kernel work", force_refresh=True)
    assert len(index_calls) == 1, "cold build should batch-embed documents once"
    first_batch_size = index_calls[0]  # 3 sessions' worth of chunks

    index_calls.clear()
    session_fts.search_sessions("kernel work", force_refresh=True)
    assert index_calls == [], f"second sync must not re-embed unchanged sessions, got {index_calls}"

    # Modify one session; only its chunks should be (re-)embedded.
    import time as _time
    _time.sleep(0.05)
    mod_path = fts_env["projects"] / "repo" / "session-0001.jsonl"
    _write_claude_jsonl(mod_path, [
        {"type": "user", "cwd": "/repo", "message": {"role": "user", "content": "updated task 1 kernel work more text"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "updated done"}]}},
    ])
    os.utime(mod_path, None)

    session_fts.search_sessions("kernel work", force_refresh=True)
    assert len(index_calls) == 1, f"expected exactly one re-embed batch for the 1 modified file, got {index_calls}"
    assert index_calls[0] < first_batch_size, "re-embedding one session should embed fewer chunks than the initial 3-session build"
