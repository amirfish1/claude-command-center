"""Extracted from server.py (originally lines 37260-37376).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from ccc_server.kilo import (
    _kilo_connect,
    _parse_kilo_conversation,
    find_kilo_conversations,
)
import sqlite3

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Antigravity integration
# ---------------------------------------------------------------------------

def _antigravity_transcript_path(session_id):
    if not session_id:
        return None
    sid = str(session_id).strip()
    if not _core._SESSION_UUID_RE.match(sid):
        return None
    for brain_root in (_core.ANTIGRAVITY_BRAIN, _core.ANTIGRAVITY_CLI_BRAIN):
        full_transcript = brain_root / sid / ".system_generated" / "logs" / "transcript_full.jsonl"
        if full_transcript.is_file():
            return full_transcript
        transcript = brain_root / sid / ".system_generated" / "logs" / "transcript.jsonl"
        if transcript.is_file():
            return transcript
    return None


def _antigravity_cli_conversation_path(session_id):
    if not session_id:
        return None
    sid = str(session_id).strip()
    if not _core._SESSION_UUID_RE.match(sid):
        return None
    # AGY writes one of two state file formats: `.pb` (older, what
    # spawn_session_antigravity typically seeds) and `.db` (newer, what
    # AGY rebuilds from the brain transcript on the first orphan resume).
    # Either one flags the session as headless-resumable.
    for suffix in (".pb", ".db"):
        candidate = _core.ANTIGRAVITY_CLI_CONVERSATIONS / f"{sid}{suffix}"
        if candidate.is_file():
            return candidate
    return _core.ANTIGRAVITY_CLI_CONVERSATIONS / f"{sid}.pb"


def _antigravity_app_conversation_path(session_id):
    if not session_id:
        return None
    sid = str(session_id).strip()
    if not _core._SESSION_UUID_RE.match(sid):
        return None
    for suffix in (".db", ".pb"):
        candidate = _core.ANTIGRAVITY_CONVERSATIONS / f"{sid}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _antigravity_transcript_paths():
    paths = []
    seen = set()
    try:
        for brain_root in (_core.ANTIGRAVITY_BRAIN, _core.ANTIGRAVITY_CLI_BRAIN):
            if not brain_root.is_dir():
                continue
            for brain_dir in brain_root.iterdir():
                if not brain_dir.is_dir():
                    continue
                full_transcript = brain_dir / ".system_generated" / "logs" / "transcript_full.jsonl"
                transcript = brain_dir / ".system_generated" / "logs" / "transcript.jsonl"
                best_transcript = full_transcript if full_transcript.is_file() else transcript
                key = str(best_transcript)
                if best_transcript.is_file() and key not in seen:
                    seen.add(key)
                    paths.append(best_transcript)
    except OSError:
        return []
    try:
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        paths.sort(key=lambda p: str(p), reverse=True)
    return paths


def _is_antigravity_session(session_id):
    path = _core._antigravity_transcript_path(session_id)
    if path and path.is_file():
        return True
    cli_path = _core._antigravity_cli_conversation_path(session_id)
    if cli_path and cli_path.is_file():
        return True
    app_path = _antigravity_app_conversation_path(session_id)
    return bool(app_path and app_path.is_file())


def _is_kilo_session(session_id):
    """Check if session_id corresponds to a Kilo Code session.

    Matches both sessions CCC spawned (in-memory registry) and external
    sessions discovered in Kilo's on-disk SQLite store — without the DB probe
    a historical or terminal-launched Kilo session would be misclassified as
    Claude and routed to the wrong transcript parser.
    """
    for s in _core._spawned_sessions:
        if s.get("engine") == "kilo" and (
            s.get("session_id") == session_id
            or s.get("resumed_sid") == session_id
            or s.get("name") == session_id
        ):
            return True
    if isinstance(session_id, str) and session_id.startswith("ses_"):
        con = _kilo_connect()
        if con is not None:
            try:
                row = con.execute(
                    "SELECT 1 FROM session WHERE id=? LIMIT 1", (session_id,)
                ).fetchone()
                if row:
                    return True
            except sqlite3.Error:
                pass
            finally:
                con.close()
    return False

