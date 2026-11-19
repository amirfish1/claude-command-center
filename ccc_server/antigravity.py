# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Extracted from server.py (originally lines 37260-37376).

Part of the server.py decomposition; see
the private plans repo (server-py-decomposition). Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from ccc_server.kilo import (
    _kilo_connect,
    _parse_kilo_conversation,
    find_kilo_conversations,
)
import sqlite3
import os
import shlex
import subprocess
import time
from pathlib import Path

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Antigravity integration
# ---------------------------------------------------------------------------

def _cli_process_logs(pid):
    """Only logs held open by this process establish terminal ownership."""
    fd_dir = Path('/proc') / str(pid) / 'fd'
    if Path('/proc').is_dir():
        paths = []
        try:
            for fd in fd_dir.iterdir():
                try:
                    paths.append(os.readlink(fd))
                except OSError:
                    pass
        except OSError:
            pass
    else:
        try:
            result = subprocess.run(
                ['lsof', '-a', '-p', str(pid), '-Fn'],
                capture_output=True, text=True, timeout=2,
            )
            paths = [line[1:] for line in result.stdout.splitlines()
                     if line.startswith('n')]
        except (OSError, subprocess.TimeoutExpired):
            return set()
    return {Path(p) for p in paths
            if Path(p).parent.name == 'log'
            and Path(p).name.startswith('cli-') and p.endswith('.log')}


def live_cli_session_ids():
    """Discover resumed and plain `agy` sessions without cwd guessing.

    Interactive launches have no conversation argument. Their own open CLI
    log supplies the current native ID, including after switching chats.
    Called inside the bulk engine scan's TTL, never once per archive row.
    """
    sids = set()
    for pid, _tty, comm, command in _core._scan_engine_processes():
        # Only argv[0] identifies the executable. Parsing unrelated agents'
        # prompts here evicts the shared token cache on every process scan.
        lexer = shlex.shlex(command, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ''
        try:
            first = lexer.get_token() or ''
        except ValueError:
            first = command.split(None, 1)[0] if command.strip() else ''
        executable = first.rsplit('/', 1)[-1]
        if not ({comm.rsplit('/', 1)[-1], executable} & {'agy', 'antigravity'}):
            continue
        owned = set()
        for log in _cli_process_logs(pid):
            sid = _core._antigravity_cli_log_meta(log).get('session_id')
            if sid and _core._SESSION_UUID_RE.fullmatch(sid):
                owned.add(sid)
        if owned:
            sids.update(owned)
            continue
        words = _core._command_tokens(command)
        for word in words:
            if (_core._SESSION_UUID_RE.fullmatch(word)
                    and _core._command_targets_engine_session(command, word, 'antigravity')):
                sids.add(word)
    return sids


def archive_terminal_overlay(rows, *, existing_ids=None):
    """Fill snapshot gaps from terminal-owned logs, without an archive scan."""
    existing = (_core._archive_overlay_existing_ids(rows)
                if existing_ids is None else existing_ids)
    archived, trashed = _core._load_conversation_lifecycle_sets()
    excluded = existing | archived | trashed
    candidates = {}
    for proc in _core.find_live_antigravity_processes():
        owned = {}
        for log in _cli_process_logs(proc['pid']):
            meta = _core._antigravity_cli_log_meta(log)
            sid = meta.get('session_id') or ''
            if _core._SESSION_UUID_RE.fullmatch(sid):
                owned[sid] = meta
        # A switched chat's log identity wins over the original resume argv.
        if not owned:
            command = proc.get('command') or ''
            for word in _core._command_tokens(command):
                if (_core._SESSION_UUID_RE.fullmatch(word)
                        and _core._command_targets_engine_session(command, word, 'antigravity')):
                    owned[word] = {}
        for sid, meta in owned.items():
            if sid not in excluded:
                candidates[sid] = (proc, meta)
    if not candidates:
        return []
    names = _core._load_session_name_overrides()
    pins = _core._load_repo_pins()
    titles = _core._load_antigravity_summary_titles()
    out = []
    for sid, (proc, meta) in candidates.items():
        path = _core._antigravity_transcript_path(sid)
        tail = _core._extract_antigravity_tail_meta(path) if path else {}
        tail = tail or {}
        cwd = pins.get(sid) or meta.get('cwd') or proc.get('cwd') or tail.get('cwd') or ''
        prompt = (tail.get('first_message') or '').strip()
        stamp = tail.get('last_meaningful_ts') or time.time()
        title = names.get(sid) or titles.get(sid) or prompt[:80] or 'Antigravity session'
        out.append({
            'id': sid, 'session_id': sid, 'source': 'antigravity', 'engine': 'antigravity',
            'display_name': title, 'first_message': prompt[:200],
            'name_overridden': bool(names.get(sid)), 'ai_title': titles.get(sid),
            'folder_path': cwd, 'folder_label': Path(cwd).name if cwd else 'Antigravity',
            'session_cwd': cwd, 'session_cwd_exists': bool(cwd and Path(cwd).is_dir()),
            'pinned_repo': bool(pins.get(sid)), 'jsonl_path': str(path) if path else '',
            'mtime': stamp, 'modified': stamp, 'is_live': True, 'spawn_pid': proc['pid'],
            'archived': False, 'trashed': False,
            'model': tail.get('model') or meta.get('model') or '',
            'last_event_type': tail.get('last_event_type'),
            'last_assistant_text': tail.get('last_assistant_text'),
            **_core._antigravity_activity_fields_from_tail(tail, True),
            'can_headless_resume': bool(
                _core._antigravity_cli_conversation_path(sid).is_file()),
            'can_app_resume': bool(_core._antigravity_app_conversation_path(sid)),
        })
    return out

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
    app_path = _core._antigravity_app_conversation_path(session_id)
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

