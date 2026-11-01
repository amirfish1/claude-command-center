"""Exact-session takeover observation and native delivery.

No replacement lineage, durable input queue, or retry watchdog lives here.
Adapters read only the requested session; receipts require persisted user input.
An adapter with no current native transport still participates in observation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time

from ccc_server import core as _core

MAX_EVENTS = 512
MAX_BYTES = 2 * 1024 * 1024
_ADAPTERS = {}
_RUNNING = {}
_LOCK = threading.RLock()
_HELP = {}
_STORE_INDEX = {}


@dataclass
class Adapter:
    """Extensions may register a reader and immediate, nonqueueing sender.

    read(sid, nonce) returns (events, metadata, receipt_evidence). Metadata
    includes source, repo_path/cwd, settings and optionally state. send must
    preserve the supplied exact session and settings; acceptance is not receipt.
    """
    engine: str
    read: object = None
    send: object = None


def register_adapter(adapter):
    _ADAPTERS[adapter.engine] = adapter


def engine_names():
    """Discover registered CCC families, without running install/help probes."""
    from ccc_server import _registry
    names = (set(getattr(_core, '_ENGINE_CURATED_MODELS', {}))
             | set(getattr(_core, '_ACP_HARNESSES', {})) | set(_ADAPTERS))
    for key in _registry:
        match = re.fullmatch(r'(?:spawn_session_([a-z][a-z0-9_]*)|_parse_([a-z][a-z0-9_]*)_conversation)', key)
        family = (match[1] or match[2]) if match else ''
        if family and (key.startswith('spawn_session_') or hasattr(_core, '_is_' + family + '_session')) and family not in ('remote', 'headless'):
            names.add(family)
    # Reader-only families are exposed by CCC too.
    names.update(('copilot', 'copilotchat'))
    names.discard('devin_cli')
    return sorted(names)


def capabilities():
    return {'schema_version': 1, 'same_session': True, 'no_queue': True,
            'observation': True, 'receipt_verification': True,
            'protocol': 'exact-session-v1', 'engines': engine_names()}


def adapter_for(engine):
    # No engine enrollment whitelist: new core readers work automatically;
    # richer adapters can register without editing the supervisor.
    return _ADAPTERS.get(engine) or Adapter(engine)


def _text(event):
    if isinstance(event.get('text'), str):
        return event['text']
    return '\n'.join(str(b.get('text') or '') for b in event.get('blocks', [])
                     if isinstance(b, dict) and b.get('kind') == 'text')


def _epoch(value):
    try:
        number = float(value)
        if number > 1e12:
            number /= 1000
        return number if math.isfinite(number) and number >= 0 else 0.0
    except (ValueError, TypeError):
        try:
            return max(0.0, datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp())
        except (ValueError, TypeError, OverflowError):
            return 0.0


def _native_sid(engine, sid):
    return sid.removeprefix('devincli-') if engine == 'devin' else sid


def _snapshot(engine, sid):
    if engine not in getattr(_core, '_ACP_HARNESSES', {}):
        return {}
    return _core._acp_session_snapshot(engine, _native_sid(engine, sid)) or {}


def _evidence(sid, nonce, source, line):
    return {'session_id': sid, 'nonce': nonce, 'role': 'user',
            'persisted': True, 'source': str(source), 'path': str(source), 'line': line}


def _tail_json(path):
    """Bound memory and disk reads even for a multi-gigabyte wire log."""
    with Path(path).open('rb') as stream:
        size = stream.seek(0, 2)
        start = max(0, size - MAX_BYTES)
        stream.seek(start)
        if start:
            stream.readline()  # never parse a truncated leading record
        lines = stream.read(MAX_BYTES).splitlines()[-MAX_EVENTS:]
    records = []
    byte_position = size - sum(len(line) + 1 for line in lines)
    for line in lines:
        byte_position += len(line) + 1
        number = max(0, byte_position)
        try:
            record = json.loads(line)
            if isinstance(record, dict):
                records.append((number, record))
        except (ValueError, UnicodeDecodeError):
            pass
    return records


def _devin_read(sid, nonce):
    raw = sid.removeprefix('devincli-')
    # Deliberately don't call the engine's ID-set/list cache for this read.
    for db in _core._devin_cli_db_paths():
        con = _core._devin_cli_connect(db)
        if con is None:
            continue
        try:
            row = con.execute('SELECT * FROM sessions WHERE id=? LIMIT 1', (raw,)).fetchone()
            if row is None:
                continue
            row = dict(row)
            rows = con.execute('SELECT row_id, chat_message, created_at FROM message_nodes '
                               'WHERE session_id=? ORDER BY row_id DESC LIMIT ?',
                               (raw, MAX_EVENTS)).fetchall()
            events, seen = [], set()
            for item in reversed(rows):
                event = _core._devin_cli_parse_message_row(item['chat_message'], item['created_at'], seen, '')
                if event:
                    event['line'] = item['row_id']
                    events.append(event)
            proof = None
            if nonce:
                # prompt_history is independently persisted native input, not
                # a CCC acceptance log or synthetic conversation bubble.
                try:
                    hit = con.execute('SELECT rowid, content FROM prompt_history WHERE session_id=? '
                                      'AND is_shell=0 AND instr(content,?)>0 ORDER BY rowid DESC LIMIT 1',
                                      (raw, nonce)).fetchone()
                    if hit:
                        proof = _evidence(sid, nonce, str(db) + '#prompt_history', hit[0])
                except sqlite3.Error:
                    pass
            mode = row.get('agent_mode') or ''
            settings = {'model': row.get('model') or '', 'permission_mode': {
                'bypass': 'dangerous', 'ask': 'auto', 'code': 'accept-edits',
            }.get(mode, mode), 'native_mode': mode}
            return events, {'source': str(db), 'cwd': row.get('working_directory') or '',
                            'settings': settings, 'activity_at': row.get('last_activity_at')}, proof
        finally:
            con.close()
    return [], {}, None


def _hermes_read(sid, nonce):
    db = _core._hermes_db_for_session(sid)
    con = _core._hermes_connect(db)
    if con is None:
        return [], {}, None
    try:
        row = con.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone()
        if not row:
            return [], {}, None
        row = dict(row)
        rows = con.execute('SELECT rowid AS takeover_rowid,* FROM messages WHERE session_id=? '
                           'ORDER BY rowid DESC LIMIT ?', (sid, MAX_EVENTS)).fetchall()
        events = []
        for msg in reversed(rows):
            msg = dict(msg)
            event = _core._parse_hermes_message(msg, msg['takeover_rowid'], row)
            if event:
                events.append(event)
        return events, {'source': str(db), 'cwd': row.get('cwd') or '',
                        'settings': {'model': row.get('model') or ''},
                        'activity_at': row.get('last_activity_at') or row.get('ended_at') or row.get('started_at')}, None
    finally:
        con.close()


def _parts_read(engine, sid):
    con = getattr(_core, '_' + engine + '_connect')()
    if con is None:
        return [], {}, None
    try:
        session = con.execute('SELECT * FROM session WHERE id=?', (sid,)).fetchone()
        if not session:
            return [], {}, None
        session = dict(session)
        messages = con.execute('SELECT id,data FROM message WHERE session_id=? '
                               'ORDER BY time_created DESC,id DESC LIMIT ?', (sid, MAX_EVENTS)).fetchall()
        events, settings = [], {}
        for item in reversed(messages):
            meta = json.loads(item['data'] or '{}')
            parts = [json.loads(r[0]) for r in con.execute(
                'SELECT data FROM part WHERE message_id=? ORDER BY time_created LIMIT ?',
                (item['id'], MAX_EVENTS))]
            role = meta.get('role')
            text = '\n'.join(p.get('text', '') for p in parts if p.get('type') == 'text')
            if role in ('user', 'assistant'):
                events.append({'type': 'user_text' if role == 'user' else 'assistant',
                               'text': text, 'line': item['id'],
                               'ts': (meta.get('time') or {}).get('completed') or (meta.get('time') or {}).get('created'),
                               'completed': role == 'assistant' and bool((meta.get('time') or {}).get('completed'))})
            model = meta.get('model')
            if not model and meta.get('modelID'):
                model = {'providerID': meta.get('providerID'), 'modelID': meta['modelID']}
            if isinstance(model, dict):
                settings['model'] = '/'.join(str(model.get(k) or '') for k in ('providerID', 'modelID'))
        return events, {'source': str(getattr(_core, '_' + engine + '_db_path')()),
                        'cwd': session.get('directory') or '', 'settings': settings,
                        'activity_at': session.get('time_updated') or session.get('time_created')}, None
    finally:
        con.close()


def _copilot_read(sid):
    source = _core._copilot_events_path(sid)
    if not source or not Path(source).is_file():
        return [], {}, None
    # Header may be outside the bounded tail, but is small and session-local.
    with Path(source).open('rb') as stream:
        header = stream.readline(65536)
    try:
        first = json.loads(header)
    except ValueError:
        first = {}
    context = (first.get('data') or {}).get('context') or first.get('data') or {}
    cwd = context.get('cwd') or context.get('workingDirectory') or ''
    events, settings = [], {}
    for number, record in _tail_json(source):
        kind = str(record.get('type') or '')
        data = record.get('data') or {}
        if not isinstance(data, dict):
            continue
        if data.get('model'):
            settings['model'] = data['model']
        if kind == 'session.start':
            context = data.get('context') or data
            cwd = context.get('cwd') or context.get('workingDirectory') or cwd
        elif kind in ('user.message', 'assistant.message'):
            events.append({'type': 'user_text' if kind == 'user.message' else 'assistant',
                           'text': _core._copilot_event_text(data), 'line': number,
                           'message_id': record.get('id'),
                           'ts': record.get('timestamp') or record.get('ts')})
    return events, {'source': str(source), 'cwd': cwd, 'settings': settings,
                    'activity_at': (_epoch(events[-1].get('ts')) if events else 0) or Path(source).stat().st_mtime}, None


def _session_file(engine, sid):
    spawn = _core._spawn_registry_entry_for_session(sid, engine) or {}
    for key in ('session_file', 'jsonl_path', 'transcript_path'):
        candidate = spawn.get(key)
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    root = (Path(os.environ.get('PI_CODING_AGENT_SESSION_DIR') or
                 Path.home() / '.pi' / 'agent' / 'sessions') if engine == 'pi'
            else Path(os.environ.get('FACTORY_HOME') or Path.home() / '.factory') / 'sessions')
    now = time.monotonic()
    cached = _STORE_INDEX.get((engine, str(root)))
    if cached is None or now - cached[0] >= 60:
        # Bounded filename discovery once per minute; no conversation reads,
        # and reconciles of known sessions only stat their exact backing file.
        found = {}
        directories, remaining = [root], 4096
        while directories and remaining > 0:
            directory = directories.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        remaining -= 1
                        if remaining < 0:
                            break
                        if entry.is_dir(follow_symlinks=False) and directory == root:
                            directories.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False) and entry.name.endswith('.jsonl'):
                            stem = entry.name[:-6]
                            found[stem] = entry.path
                            if engine == 'pi' and '_' in stem:
                                found[stem.split('_', 1)[1]] = entry.path
            except OSError:
                continue
        cached = (now, found)
        _STORE_INDEX[(engine, str(root))] = cached
    candidate = cached[1].get(sid)
    return Path(candidate) if candidate and Path(candidate).is_file() else None


def _session_jsonl_read(engine, sid):
    # Pi native format: https://github.com/earendil-works/pi-mono/blob/main/packages/coding-agent/docs/session-format.md
    # Droid stores retain their native role/message records; unknown records
    # never become assistant completion or user-input evidence.
    source = _session_file(engine, sid)
    if source is None:
        return [], {}, None
    with source.open('rb') as stream:
        first = stream.readline(65536)
    try:
        header = json.loads(first)
    except ValueError:
        return [], {}, None
    header_id = header.get('sessionId') or header.get('session_id') or header.get('id')
    if header_id and header_id != sid:
        return [], {}, None
    cwd = header.get('cwd') or header.get('workingDirectory') or ''
    settings, events = {}, []
    for number, record in _tail_json(source):
        kind = record.get('type')
        if kind == 'model_change':
            model = record.get('modelId') or ''
            provider = record.get('provider')
            settings['model'] = (provider + '/' if provider else '') + model
        elif kind == 'thinking_level_change':
            settings['reasoning_effort'] = record.get('thinkingLevel') or ''
        elif kind in ('settings', 'session_settings'):
            for key in ('model', 'reasoning_effort', 'permission_mode', 'auto'):
                if record.get(key) is not None:
                    settings[key] = record[key]
        payload = record.get('message')
        if not isinstance(payload, dict):
            if record.get('role') in ('user', 'assistant'):
                payload = record
            else:
                continue
        role = payload.get('role')
        if role not in ('user', 'assistant'):
            continue
        content = payload.get('content') or ''
        blocks = content if isinstance(content, list) else []
        text = content if isinstance(content, str) else ''.join(
            str(block.get('text') or '') for block in blocks
            if isinstance(block, dict) and block.get('type') == 'text')
        stop = payload.get('stopReason') or payload.get('stop_reason')
        complete = (role == 'assistant' and stop in ('stop', 'end_turn', 'max_tokens'))
        if not complete and engine == 'droid':
            complete = role == 'assistant' and not any(b.get('type') in ('tool_use', 'toolCall') for b in blocks)
        event = {'type': 'user_text' if role == 'user' else 'assistant',
                 'text': text, 'line': number, 'message_id': record.get('id'),
                 'ts': record.get('timestamp') or payload.get('timestamp'), 'completed': complete}
        events.append(event)
        if payload.get('model') and not settings.get('model'):
            provider = payload.get('provider') if engine == 'pi' else ''
            settings['model'] = (str(provider) + '/' if provider else '') + str(payload['model'])
        cwd = record.get('cwd') or cwd
    return events, {'source': str(source), 'cwd': cwd, 'settings': settings,
                    'activity_at': source.stat().st_mtime}, None


def _kimi_read(sid, nonce):
    index = _core._kimi_session_index()
    entry = index.get(sid) or index.get('session_' + sid) or {}
    directory = entry.get('session_dir')
    if not directory:
        return _file_read('kimi', sid) or ([], {}, None)
    wire = Path(directory) / 'agents' / 'main' / 'wire.jsonl'
    events, text, settings, active = [], '', {}, False
    for number, record in _tail_json(wire):
        kind = record.get('type')
        if kind == 'config.update':
            if record.get('modelAlias'):
                settings['model'] = record['modelAlias']
            for key in ('mode', 'reasoningEffort'):
                if record.get(key) is not None:
                    settings[key] = record[key]
        elif kind == 'turn.prompt':
            prompt = ''.join(str(p.get('text') or '') for p in record.get('input') or [] if isinstance(p, dict) and p.get('type') == 'text')
            events.append({'type': 'user_text', 'text': prompt, 'line': number})
            text, active = '', True
        elif kind == 'context.append_message':
            message = record.get('message') or {}
            if message.get('role') == 'user':
                content = message.get('content') or ''
                if isinstance(content, list):
                    content = ''.join(str(p.get('text') or '') for p in content if isinstance(p, dict))
                events.append({'type': 'user_text', 'text': str(content), 'line': number})
        elif kind == 'context.append_loop_event':
            loop = record.get('event') or {}
            part = loop.get('part') or {}
            if loop.get('type') == 'content.part' and part.get('type') == 'text':
                text += str(part.get('text') or '')
            elif loop.get('type') == 'step.end' and loop.get('finishReason') != 'tool_use':
                if text:
                    events.append({'type': 'assistant', 'text': text, 'completed': True, 'line': number})
                text, active = '', False
        elif kind == 'turn.cancel':
            text, active = '', False
    return events, {'source': str(wire), 'cwd': entry.get('work_dir') or '',
                    'settings': settings, 'state': 'busy' if active else 'idle',
                    'activity_at': wire.stat().st_mtime}, None


def _grok_read(sid):
    """Native Grok DB is queried by exact key; small native files use CCC's parser."""
    connect = getattr(_core, '_grok_db_connect', None)
    con = connect() if connect else None
    if con is not None:
        try:
            row = con.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone()
            if row:
                session = dict(row)
                cols = {r['name'] for r in con.execute('PRAGMA table_info(messages)')}
                choose = lambda names: next((name for name in names if name in cols), None)
                sid_col = choose(('session_id', 'sessionId', 'session'))
                body_col = choose(('message_json', 'content', 'text', 'message'))
                order = choose(('seq', 'created_at', 'id')) or 'rowid'
                ts = choose(('created_at', 'timestamp', 'ts'))
                if not sid_col or not body_col:
                    return [], {'source': str(_core._grok_db_path()), 'state': 'unknown'}, None
                # Identifiers come solely from constant allowlists above.
                rows = con.execute(f'SELECT rowid AS takeover_rowid,* FROM messages WHERE {sid_col}=? '
                                   f'ORDER BY {order} DESC LIMIT ?', (sid, MAX_EVENTS)).fetchall()
                events = []
                for item in reversed(rows):
                    item = dict(item)
                    role = item.get('role')
                    if role not in ('user', 'assistant', 'tool'):
                        continue
                    text = _core._grok_db_message_text(item.get(body_col))
                    events.append({'type': {'user': 'user_text', 'assistant': 'assistant', 'tool': 'tool_result'}[role],
                                   'text': text, 'line': item['takeover_rowid'], 'ts': item.get(ts)})
                cwd = session.get('cwd_last') or session.get('cwd_at_start') or session.get('cwd') or ''
                if not cwd and session.get('workspace_id'):
                    workspace = con.execute('SELECT * FROM workspaces WHERE id=?', (session['workspace_id'],)).fetchone()
                    cwd = dict(workspace).get('canonical_path') or '' if workspace else ''
                return events, {'source': str(_core._grok_db_path()), 'cwd': cwd,
                                'settings': {'model': session.get('model') or session.get('model_id') or ''},
                                'activity_at': session.get('updated_at') or session.get('last_activity_at')}, None
        finally:
            con.close()
    directory = getattr(_core, '_grok_session_dir', lambda sid: None)(sid)
    if directory is None:
        return None
    source = _core._grok_session_jsonl(directory)
    if source is None:
        return None
    source = Path(source)
    if source.stat().st_size > MAX_BYTES:
        return [], {'source': str(source), 'state': 'unknown'}, None
    parsed = _core._parse_grok_conversation(sid, after_line=0)
    meta = {'source': str(source), 'activity_at': source.stat().st_mtime}
    summary = Path(directory) / 'summary.json'
    if summary.is_file() and summary.stat().st_size <= 65536:
        data = json.loads(summary.read_text())
        meta.update(cwd=data.get('cwd') or data.get('working_directory') or '',
                    settings={'model': data.get('current_model_id') or data.get('model') or data.get('model_id') or ''})
    return list(parsed.get('events') or [])[-MAX_EVENTS:], meta, None


def _file_read(engine, sid):
    """Use native per-record parsers, never the UI's synthetic queued events."""
    parser = None
    if engine == 'claude':
        path, parser = _core._resolve_conversation_path(sid), _core._parse_conversation_event
    elif engine == 'codex':
        path, parser = _core._resolve_codex_rollout_path(sid), _core._parse_codex_event
    elif engine == 'cursor':
        path, parser = _core._cursor_transcript_path(sid), _core._parse_cursor_event
    elif engine == 'antigravity':
        path, parser = _core._antigravity_transcript_path(sid), _core._parse_antigravity_event
    elif engine in getattr(_core, '_ACP_HARNESSES', {}):
        path = _core._acp_transcript_path(engine, sid)
        if not path or not Path(path).is_file():
            return None
    elif engine == 'aider':
        path, parser = _core._aider_session_path(sid), _core._parse_conversation_event
    else:
        return None
    if not path or not Path(path).is_file():
        return [], {}, None
    events, settings, cwd = [], {}, ''
    for number, record in _tail_json(path):
        if parser:
            parsed = parser(record, number)
            items = parsed if isinstance(parsed, list) else [parsed]
        else:
            items = [record]
        for event in items:
            if isinstance(event, dict):
                events.append(event)
        payload = record.get('payload') or record.get('message') or record
        if isinstance(payload, dict):
            cwd = payload.get('cwd') or cwd
            for key in ('model', 'reasoning_effort', 'permission_mode', 'approval_policy', 'sandbox_policy', 'runtime'):
                if payload.get(key) is not None:
                    settings[key] = payload[key]
    return events, {'source': str(path), 'cwd': cwd, 'settings': settings,
                    'acceptance_only': engine in getattr(_core, '_ACP_HARNESSES', {}) or engine == 'aider',
                    'activity_at': (_epoch(events[-1].get('ts')) if events else 0) or Path(path).stat().st_mtime}, None


def _read(adapter, sid, nonce):
    if adapter.read:
        return adapter.read(sid, nonce)
    engine = adapter.engine
    if engine == 'devin' and sid.startswith('devincli-'):
        return _devin_read(sid, nonce)
    if engine == 'hermes':
        return _hermes_read(sid, nonce)
    if engine == 'copilot':
        return _copilot_read(sid)
    if engine == 'kimi':
        return _kimi_read(sid, nonce)
    if engine == 'grok':
        native = _grok_read(sid)
        if native is not None:
            return native
    if engine in ('pi', 'droid'):
        return _session_jsonl_read(engine, sid)
    if engine in ('opencode', 'kilo'):
        return _parts_read(engine, sid)
    found = _file_read(engine, sid)
    if found is not None:
        return found
    # Future registered CCC parser families participate automatically. These
    # readers query one session, never find_*_conversations list discovery.
    parser = getattr(_core, '_parse_' + engine + '_conversation', None)
    if parser:
        source = _core.conversation_transcript_path(sid)
        if source and Path(source).is_file() and Path(source).stat().st_size > MAX_BYTES:
            return [], {'source': str(source), 'state': 'unknown'}, None
        parsed = parser(sid, after_line=0)
        events = list(parsed.get('events') or [])[-MAX_EVENTS:]
        meta = {'source': source or 'native:' + engine, 'settings': {}}
        for event in events:
            if event.get('model'):
                meta['settings']['model'] = event['model']
        if source and Path(source).is_file() and Path(source).suffix == '.json':
            try:
                payload = json.loads(Path(source).read_text())
                if isinstance(payload, dict):
                    meta['cwd'] = payload.get('cwd') or payload.get('workingDirectory') or payload.get('projectRoot') or ''
                    for key in ('model', 'reasoning_effort', 'permission_mode', 'approval_mode'):
                        if payload.get(key) is not None:
                            meta['settings'][key] = payload[key]
            except (OSError, ValueError):
                pass
        return events, meta, None
    return [], {}, None


def _state(sid, engine, meta, events):
    with _LOCK:
        proc = _RUNNING.get(sid)
        if proc is not None:
            if proc.poll() is None:
                return 'busy'
            _RUNNING.pop(sid, None)
    snap = _snapshot(engine, sid)
    if snap.get('pending_permissions') or snap.get('status') == 'active':
        return 'busy'
    if snap.get('loaded') and snap.get('status') == 'idle':
        return 'idle'
    if engine == 'antigravity':
        from ccc_server import antigravity_stream
        with antigravity_stream._LOCK:
            stream = antigravity_stream._PROCS.get(sid)
            if stream is not None and stream.alive():
                return 'busy' if stream.busy else 'idle'
    for entry in list(getattr(_core, '_spawned_sessions', [])):
        if sid in (entry.get('session_id'), entry.get('resumed_sid')):
            if _core._poll_spawn_entry(entry) is None:
                if entry.get('fifo') and entry.get('engine') == 'claude' and not _core._headless_turn_in_progress(entry):
                    return 'idle'
                return 'busy'
    stored = _core._spawn_registry_entry_for_session(sid, engine) or {}
    if stored.get('pid') and getattr(_core, '_pid_alive', lambda pid: False)(stored['pid']):
        return 'busy'
    if engine == 'devin' and sid.startswith('devincli-'):
        if _core._devin_cli_session_live(sid.removeprefix('devincli-')):
            return 'busy'
    if engine == 'hermes':
        live = _core._hermes_active_session_status(sid) or {}
        if live.get('live') or live.get('is_live') or live.get('pid'):
            return 'busy'
    if engine == 'grok' and getattr(_core, '_grok_external_writer_active', lambda sid: False)(sid):
        return 'unknown'
    if engine == 'claude':
        status = _core.session_live_status(sid, meta.get('cwd') or '') or {}
        if status.get('live'):
            return 'unknown'
    if meta.get('state') in ('busy', 'idle', 'unknown'):
        return meta['state']
    # A persisted final assistant reply with no outstanding tool call is
    # positive idle evidence. A recent user input without a final reply is
    # unknown: never start another writer on the basis of age alone.
    for event in reversed(events):
        if event.get('type') in ('user_text', 'user'):
            return 'unknown'
        if event.get('type') == 'assistant' and _text(event):
            blocks = event.get('blocks') or []
            if any(b.get('kind') in ('tool_use', 'tool_call') for b in blocks if isinstance(b, dict)):
                return 'unknown'
            if event.get('completed') is False:
                return 'unknown'
            return 'idle'
    return 'unknown'


def observe(session_id, engine='', nonce=''):
    sid = str(session_id or '').strip()
    engine = str(engine or '').strip().lower()
    result = {'schema_version': 1, 'session_id': sid, 'engine': engine,
              'host': socket.gethostname(), 'repo_path': '', 'transcript': '',
              'cursor': '', 'completed_turn': '', 'completed_text': '',
              'activity_at': 0.0, 'state': 'unknown', 'footer': None,
              'settings': {}, 'cwd': '', 'observed_at': time.time(),
              'source': 'ccc-takeover', 'error': None, 'receipt_confirmed': False,
              'receipt_evidence': None}
    if not sid or len(sid) > 256 or not re.fullmatch(r'[A-Za-z0-9_.:-]+', sid):
        result['error'] = 'invalid session_id'
        return result
    if not engine:
        engine = _core._detect_session_engine(sid)
        result['engine'] = engine
    if not re.fullmatch(r'[a-z][a-z0-9_-]*', engine):
        result['error'] = 'invalid engine'
        return result
    try:
        adapter = adapter_for(engine)
        events, meta, proof = _read(adapter, sid, str(nonce or ''))
        spawn = _core._spawn_registry_entry_for_session(sid, engine) or {}
        settings = {k: spawn[k] for k in ('model', 'reasoning_effort', 'permission_mode', 'permissions', 'runtime') if spawn.get(k) is not None}
        settings.update(meta.get('settings') or {})
        snap = _snapshot(engine, sid)
        # Native session settings are authoritative. ACP facade state may
        # retain an ambient default even for a loaded session; disconnected
        # state cannot supply current configuration at all.
        if snap.get('loaded') and snap.get('model') and not settings.get('model'):
            settings['model'] = snap['model']
        for option in (snap.get('config_options') or []) if snap.get('loaded') else []:
            if not isinstance(option, dict):
                continue
            key = option.get('category') or option.get('id')
            value = option.get('currentValue')
            if key and value is not None and settings.get(str(key)) in (None, ''):
                settings[str(key)] = value
        settings.update(_core._get_session_override(sid) or {})
        cwd = meta.get('cwd') or spawn.get('cwd') or ''
        result.update(repo_path=meta.get('repo_path') or spawn.get('repo_path') or cwd,
                      transcript=meta.get('source') or '', settings=settings, cwd=cwd,
                      source=meta.get('source') or 'native:' + engine,
                      activity_at=meta.get('activity_at'), state=_state(sid, engine, meta, events))
        if events:
            result['cursor'] = str(events[-1].get('line') or len(events))
            result['activity_at'] = result['activity_at'] or events[-1].get('ts')
        for event in reversed(events):
            if event.get('type') != 'assistant' or event.get('subagent_report') or event.get('completed') is False:
                continue
            text = _text(event)
            if not text or any(b.get('kind') in ('tool_use', 'tool_call') for b in event.get('blocks', []) if isinstance(b, dict)):
                continue
            result['completed_text'] = text
            result['completed_turn'] = str(event.get('message_id') or event.get('line') or '')
            result['footer'] = {'completed_text': text}
            break
        if nonce and not proof:
            for event in reversed(events):
                if event.get('type') in ('user_text', 'user') and not event.get('synthetic') and not event.get('takeover_pending') and not meta.get('acceptance_only') and str(nonce) in _text(event):
                    proof = _evidence(sid, str(nonce), result['source'], event.get('line'))
                    break
        if proof and nonce:
            # Enforce proof contract even for extension adapters.
            valid = (proof.get('session_id') == sid and proof.get('nonce') == nonce
                     and proof.get('role') == 'user' and proof.get('persisted') is True and proof.get('source'))
            if valid:
                result.update(receipt_confirmed=True, receipt_evidence=proof)
        result['activity_at'] = _epoch(result.get('activity_at'))
        if not meta and not events:
            result['error'] = 'No persisted session evidence available from the registered adapter'
    except Exception as exc:
        result.update(state='unknown', error=str(exc))
    return result


def _aider_history(sid):
    source = _core._aider_session_path(sid)
    return Path(source).with_suffix('.takeover.chat.history.md')


def _help(engine, binary):
    key = (engine, binary)
    if key not in _HELP:
        try:
            output = subprocess.run([binary, '--help'], capture_output=True, text=True, timeout=5)
            _HELP[key] = (output.stdout + output.stderr)[:65536]
        except (OSError, subprocess.TimeoutExpired):
            _HELP[key] = ''
    return _HELP[key]


def native_command(engine, sid, text, settings, cwd):
    """Build exact native resume argv; never add auto-approval/global defaults."""
    resolver = getattr(_core, '_resolve_' + engine + '_bin', None)
    resolved = resolver() if resolver else {'available': bool(shutil.which(engine)), 'bin': shutil.which(engine)}
    if not resolved.get('available'):
        raise ValueError(resolved.get('reason') or engine + ' native binary unavailable')
    binary = resolved['bin']
    if engine == 'devin':
        if not sid.startswith('devincli-'):
            raise ValueError('Cloud session requires a registered remote delivery adapter')
        cmd = [binary]
        if settings.get('config_path'):
            cmd += ['--config', str(settings['config_path'])]
        cmd += ['--resume', sid.removeprefix('devincli-')]
        if isinstance(settings.get('respect_workspace_trust'), bool):
            cmd += ['--respect-workspace-trust', str(settings['respect_workspace_trust']).lower()]
        mode = settings.get('permission_mode')
        if mode:
            cmd += ['--permission-mode', mode]
        cmd += ['-p', text]
    elif engine == 'aider':
        history = _aider_history(sid)
        if not history.is_file():
            source = _core._aider_session_path(sid)
            if not source or Path(source).stat().st_size > MAX_BYTES:
                raise ValueError('Native Aider history requires a bounded existing transcript')
            contents = []
            for record in _core._aider_read_records(source):
                message = record.get('message') or {}
                role = message.get('role')
                blocks = message.get('content') or []
                body = blocks if isinstance(blocks, str) else ''.join(
                    str(b.get('text') or '') for b in blocks if isinstance(b, dict) and b.get('type') == 'text')
                if role == 'user':
                    contents.append('\n#### ' + '  \n#### '.join(body.splitlines()) + '\n\n')
                elif role == 'assistant':
                    contents.append(body + '\n\n')
            history.parent.mkdir(parents=True, exist_ok=True)
            history.write_text(''.join(contents))
        cmd = [binary, '--chat-history-file', str(history), '--restore-chat-history',
               '--message', text, '--no-pretty']
        # CCC's Aider adapter creates these sessions with --yes-always.
        cmd += ['--yes-always']
    elif engine == 'droid':
        # https://docs.factory.com/droid-exec/overview (same-session, no --fork)
        cmd = [binary, 'exec', '--session-id', sid, '--output-format', 'stream-json']
        if settings.get('auto') in ('low', 'medium', 'high'):
            cmd += ['--auto', settings['auto']]
        if settings.get('reasoning_effort'):
            cmd += ['--reasoning-effort', str(settings['reasoning_effort'])]
        cmd += [text]
    elif engine == 'pi':
        # Exact file prevents Pi's cross-workspace selector/fork behavior.
        source = _session_file(engine, sid)
        if source is None:
            raise ValueError('Original native Pi session file is unavailable')
        cmd = [binary, '--session', str(source), '--print', text]
        if settings.get('reasoning_effort'):
            cmd += ['--thinking', str(settings['reasoning_effort'])]
    elif engine == 'claude':
        cmd = [binary, '--resume', sid, '--print', text, '--output-format', 'stream-json', '--verbose']
        if settings.get('permission_mode'):
            cmd += ['--permission-mode', settings['permission_mode']]
    elif engine == 'cursor':
        cmd = [binary, '--resume', sid, '--print', '--output-format', 'stream-json', '--workspace', cwd, text]
    elif engine == 'hermes':
        cmd = [binary, 'chat', '--resume', sid, '--query', text, '--quiet']
    elif engine in ('kilo', 'opencode'):
        cmd = [binary, 'run', '--session', sid, text]
    elif engine == 'kimi':
        cmd = [binary, '--session', sid, '--prompt', text]
    elif engine in ('grok', 'gemini', 'copilot'):
        cmd = [binary, '--resume', sid, '-p', text]
        if engine == 'gemini' and settings.get('approval_mode'):
            cmd += ['--approval-mode', str(settings['approval_mode'])]
    elif engine == 'antigravity':
        path = _core._antigravity_cli_conversation_path(sid)
        if not path or not Path(path).is_file():
            raise ValueError('Session is not in the native CLI conversation store')
        cmd = _core._antigravity_command_words(resolved) + ['--conversation', sid, '-p', text]
    else:
        # Future native engines are accepted through capability discovery,
        # rather than accidentally invoking Claude on their session IDs.
        help_text = _help(engine, binary)
        flag = '--session' if '--session ' in help_text else '--resume' if '--resume ' in help_text else ''
        if not flag or not ('--print' in help_text or '--prompt' in help_text):
            raise ValueError('Registered adapter has no proven exact-session headless invocation')
        prompt_flag = '--prompt' if '--prompt' in help_text else '--print'
        cmd = [binary, flag, sid, prompt_flag, text]
    if settings.get('model') and engine not in ('antigravity',):
        cmd += ['--model', str(settings['model'])]
    effort = settings.get('reasoning_effort')
    if effort and engine not in ('droid', 'pi'):
        help_text = _help(engine, binary)
        flag = next((f for f in ('--reasoning-effort', '--effort', '--reasoning') if f in help_text), None)
        if flag:
            cmd += [flag, str(effort)]
        elif engine != 'devin':
            raise ValueError('Native resume cannot preserve explicit reasoning effort')
    return cmd


def deliver(session_id, text, engine='', *, local=False):
    """Single immediate attempt, guarded by an interprocess exact-SID lock."""
    def refuse(code, message, **extra):
        return {'ok': False, 'same_session': False, 'queued': False, 'code': code, 'error': message, **extra}
    if not isinstance(text, str) or not text.strip():
        return refuse('invalid_scoped_delivery', 'Missing takeover input')
    sid = str(session_id)
    key = hashlib.sha256(sid.encode()).hexdigest()
    lock_dir = Path(tempfile.gettempdir()) / ('ccc-takeover-' + str(os.getuid()))
    lock_dir.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(lock_dir / (key + '.lock'), os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return refuse('session_busy', 'Another takeover attempt owns this session')
        observation = observe(sid, engine)
        engine = observation['engine']
        if observation['state'] != 'idle':
            return refuse('session_' + observation['state'], observation.get('error') or 'Session is not provably idle')
        if engine in getattr(_core, '_ACP_HARNESSES', {}):
            if not local and engine in getattr(_core, '_ACP_WORKER_HARNESSES', ()):
                fcntl.flock(fd, fcntl.LOCK_UN)
                routed = _core._control_plane_engine_call(
                    engine, 'takeover_deliver', {'session_id': sid, 'text': text}, mutate=False)
                if routed is not None:
                    # Timeouts are ambiguous. Never fall through after an
                    # owner received the request (even if it returned error).
                    return routed
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return refuse('session_busy', 'Another operation owns this session')
            current = _snapshot(engine, sid)
            if current.get('loaded'):
                return _deliver_acp(engine, sid, text)
        if engine == 'antigravity':
            from ccc_server import antigravity_stream
            with antigravity_stream._LOCK:
                stream = antigravity_stream._PROCS.get(sid)
            if stream is not None and stream.alive():
                result = antigravity_stream.send(sid, text)
                return dict(result, session_id=sid, same_session=bool(result.get('ok')),
                            queued=False, receipt_confirmed=False)
        if engine == 'claude':
            for entry in list(_core._spawned_sessions):
                if sid in (entry.get('session_id'), entry.get('resumed_sid')) and entry.get('fifo'):
                    if _core._poll_spawn_entry(entry) is None:
                        if _core._headless_turn_in_progress(entry):
                            return refuse('session_busy', 'A Claude turn started before delivery')
                        sent = _core._write_fifo_line_once(entry['fifo'], text)
                        return {'ok': bool(sent), 'same_session': bool(sent), 'queued': False,
                                'session_id': sid, 'via': 'takeover-fifo', 'receipt_confirmed': False,
                                'uncertain': not sent}
        adapter = adapter_for(engine)
        if adapter.send:
            return adapter.send(sid, text, observation)
        cwd = observation['cwd']
        if not cwd or not Path(cwd).is_dir():
            return refuse('session_context_unavailable', 'Original working directory is unavailable')
        try:
            cmd = native_command(engine, sid, text, observation['settings'], cwd)
        except ValueError as exc:
            return refuse('scoped_transport_unavailable', str(exc))
        # Session-specific model/effort are authoritative, not env defaults.
        env = dict(os.environ)
        if observation['settings'].get('runtime') == 'free':
            from ccc_server import free_runtime
            overlay = free_runtime.spawn_env(engine, model=observation['settings'].get('model') or '')
            if not overlay:
                return refuse('scoped_transport_unavailable', 'Original free runtime is unavailable')
            env.update(overlay)
            free_runtime.scrub_paid_env(env)
        for name in list(env):
            if name in (engine.upper() + '_MODEL', engine.upper() + '_REASONING_EFFORT', engine.upper() + '_PERMISSION_MODE'):
                env.pop(name)
        log_dir = _core.repo_log_dir(cwd)
        log_dir.mkdir(parents=True, exist_ok=True)
        log = log_dir / ('takeover-' + key[:16] + '-' + str(time.time_ns()) + '.log')
        with log.open('wb') as output:
            proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        with _LOCK:
            _RUNNING[sid] = proc
        _core._spawned_sessions.append({'pid': proc.pid, 'proc': proc, 'engine': engine,
                                       'session_id': sid, 'resumed_sid': sid,
                                       'cwd': cwd, 'repo_path': cwd, 'log': str(log),
                                       'takeover': True, **observation['settings']})
        if engine == 'aider':
            source = _core._aider_session_path(sid)
            _core._aider_append(source, _core._aider_user_event(sid, text))
            entry = _core._spawned_sessions[-1]
            threading.Thread(target=_core._aider_finish_turn, args=(entry, source), daemon=True).start()
        recorder = getattr(_core, '_record_spawn_to_registry', None)
        if recorder:
            recorder(pid=proc.pid, name='takeover-' + engine, log_path=log, cwd=cwd,
                     spawned_at=time.strftime('%Y%m%dT%H%M%S'), command_summary=text[:200],
                     fifo=None, engine=engine, session_id=sid, repo_path=observation['repo_path'],
                     model=observation['settings'].get('model'),
                     reasoning_effort=observation['settings'].get('reasoning_effort'))
        # This is acceptance only. No receipt, landed claim, retry or queue.
        return {'ok': True, 'same_session': True, 'queued': False, 'session_id': sid,
                'engine': engine, 'pid': proc.pid, 'via': 'native-exact-resume',
                'log': str(log), 'receipt_confirmed': False, 'accepted': True}
    except (OSError, ValueError) as exc:
        return refuse('scoped_transport_unavailable', str(exc))
    finally:
        os.close(fd)


def _deliver_acp(engine, sid, text):
    """Use only an already loaded exact session on its current connection.

    Install pending state before exposing the request to the reader. The
    connection object is held through send: no ensure/load/new or fallback.
    The existing finalizer sees an empty retry payload even on remote-busy
    errors; takeover intent never enters its automatic retry queue.
    """
    raw = _native_sid(engine, sid)
    with _core._ACP_LOCK:
        conn = _core._ACP_CONNS.get(engine)
        transport = (conn or {}).get('transport')
        state = (_core._ACP_SESSION_STATE.get(engine) or {}).get(raw)
        if (not state or not conn or not transport or not transport.alive()
                or state.get('loaded_conn') != id(conn)
                or _core._ACP_CONNS.get(engine) is not conn):
            return {'ok': False, 'same_session': False, 'queued': False,
                    'code': 'scoped_transport_unavailable', 'error': 'No current exact-session ACP connection'}
        if (state.get('status') != 'idle' or state.get('active_turn')
                or state.get('pending_permissions')):
            return {'ok': False, 'same_session': False, 'queued': False,
                    'code': 'session_busy', 'error': 'ACP session has an unresolved operation'}
        req_id = conn['next_id']
        conn['next_id'] += 1
        entry = {'event': threading.Event(), 'response': None, 'method': 'session/prompt',
                 'sid': raw, 'req_id': req_id, 'is_active': True, 'takeover': True}
        _core._ACP_PENDING.setdefault(engine, {})[req_id] = entry
        state['turn_seq'] = state.get('turn_seq', 0) + 1
        state['active_turn'] = {
            'req_id': req_id, 'msg_id': f"takeover-{engine}-{req_id}", 'text': '',
            'thought': '', 'tools': {}, 'prompt': '', 'from_queue': False,
            'started_at': time.time(), 'wire_offset': None, 'no_queue': True,
        }
        state['status'] = 'active'
        state.setdefault('deltas', []).clear()
        try:
            # Send through this exact object, not _acp_send's engine lookup.
            transport.send_json({'jsonrpc': '2.0', 'id': req_id, 'method': 'session/prompt',
                                 'params': {'sessionId': raw, 'prompt': [{'type': 'text', 'text': text}]}})
        except Exception:
            # Keep unresolved state: a write error may follow a partial send.
            # Reconciliation, never blind fallback, resolves this uncertainty.
            return {'ok': False, 'same_session': False, 'queued': False,
                    'code': 'delivery_unknown', 'uncertain': True,
                    'error': 'ACP write outcome is unknown; reconcile before retry'}
        return {'ok': True, 'same_session': True, 'queued': False, 'session_id': sid,
                'native_session_id': raw, 'engine': engine, 'req_id': req_id,
                'via': 'takeover-acp', 'accepted': True, 'receipt_confirmed': False}
