"""Idempotent, incremental ingestion of the three engines into the usage DB."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, Optional

from . import schema
from .adapters import ADAPTERS
from .models import family_and_label, pricing_key
from .types import ParsedSession, SourceFile

_COUNTERS = ("discovered", "inserted", "updated", "unchanged", "skipped", "failed")


class Report:
    def __init__(self):
        self.engines: Dict[str, Dict[str, int]] = defaultdict(lambda: {c: 0 for c in _COUNTERS})
        self.details: list = []  # (engine, kind, path, reason)
        self.events_added = 0
        self.duplicate_events_skipped = 0
        self.missing_source_files: Dict[str, int] = defaultdict(int)

    def note(self, engine, kind, path, reason):
        self.details.append((engine, kind, path, reason))

    def as_dict(self):
        return {
            "engines": {k: dict(v) for k, v in self.engines.items()},
            "events_added": self.events_added,
            "duplicate_events_skipped": self.duplicate_events_skipped,
            "missing_source_files": dict(self.missing_source_files),
            "details": [
                {"engine": e, "kind": k, "path": p, "reason": r} for e, k, p, r in self.details
            ],
        }


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_from_ns(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _duration(a: Optional[str], b: Optional[str]) -> Optional[int]:
    if not a or not b:
        return None
    try:
        fa = datetime.fromisoformat(a.replace("Z", "+00:00"))
        fb = datetime.fromisoformat(b.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(int((fb - fa).total_seconds()), 0)


def _project_name(cwd: Optional[str]) -> Optional[str]:
    if not cwd:
        return None
    name = os.path.basename(cwd.rstrip("/"))
    return name or None


_FINGERPRINT_COLS = (
    "model_id, model_count, message_count, tool_call_count, duration_seconds, "
    "input_tokens, cache_read_input_tokens, cache_creation_input_tokens, output_tokens, "
    "reasoning_tokens, usage_event_count, compaction_count, usage_complete, warning, "
    "last_activity_at, status, project_name, git_branch, parent_source_session_id, "
    "source_format_version, raw_metadata_json"
)


def _fingerprint(conn, sid: int):
    return tuple(conn.execute(f"SELECT {_FINGERPRINT_COLS} FROM sessions WHERE id=?", (sid,)).fetchone())


def _upsert_session(conn, ps: ParsedSession, sf: SourceFile, ingested_at: str):
    """Insert/refresh the session row's source-derived fields. Returns (id, existed)."""
    row = conn.execute(
        "SELECT id FROM sessions WHERE engine=? AND source_session_id=?",
        (ps.engine, ps.source_session_id),
    ).fetchone()
    fields = dict(
        provider=ps.provider,
        parent_source_session_id=ps.parent_source_session_id,
        is_subagent=int(ps.is_subagent),
        agent_label=ps.agent_label,
        project_name=_project_name(ps.working_directory),
        working_directory=ps.working_directory,
        git_branch=ps.git_branch,
        started_at=ps.started_at,
        last_activity_at=ps.last_activity_at,
        status=ps.status,
        user_message_count=ps.user_message_count,
        assistant_message_count=ps.assistant_message_count,
        message_count=ps.user_message_count + ps.assistant_message_count,
        tool_call_count=ps.tool_call_count,
        duration_seconds=_duration(ps.started_at, ps.last_activity_at),
        compaction_count=ps.compaction_count,
        source_path=ps.source_path,
        source_size=sf.size,
        source_format_version=ps.source_format_version,
        source_modified_at=_iso_from_ns(sf.mtime_ns),
        ingested_at=ingested_at,
        raw_metadata_json=json.dumps(ps.metadata, sort_keys=True) if ps.metadata else None,
        usage_complete=int(ps.usage_complete),
    )
    if row:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE sessions SET {sets} WHERE id=?", (*fields.values(), row["id"]))
        return row["id"], True
    cols = ", ".join(("engine", "source_session_id", *fields))
    marks = ", ".join("?" * (2 + len(fields)))
    cur = conn.execute(
        f"INSERT INTO sessions ({cols}) VALUES ({marks})",
        (ps.engine, ps.source_session_id, *fields.values()),
    )
    return cur.lastrowid, False


def _claim_events(conn, session_id: int, ps: ParsedSession, report: Report, dirty: set):
    """Replace the events read from this file. A duplicated event (same engine + key) stays
    with whichever session started first (ties: smaller source id), so totals are
    the same regardless of ingestion order and nothing is counted twice."""
    conn.execute(
        "DELETE FROM usage_events WHERE session_id=? AND source_path=?", (session_id, ps.source_path)
    )
    my_rank = (ps.started_at or "9999", ps.source_session_id)
    for e in ps.events:
        cur = conn.execute(
            "INSERT OR IGNORE INTO usage_events (session_id, engine, event_key, ts, model_id, "
            "pricing_key, input_tokens, cache_read_tokens, cache_creation_tokens, "
            "cache_creation_1h_tokens, output_tokens, reasoning_tokens, scope, source_path) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                session_id, ps.engine, e.event_key, e.ts, e.model_id, pricing_key(e.model_id),
                e.input_tokens, e.cache_read_tokens, e.cache_creation_tokens,
                e.cache_creation_1h_tokens, e.output_tokens, e.reasoning_tokens, e.scope,
                ps.source_path,
            ),
        )
        if cur.rowcount:
            report.events_added += 1
            continue
        owner = conn.execute(
            "SELECT u.id, u.session_id, s.started_at, s.source_session_id FROM usage_events u "
            "JOIN sessions s ON s.id=u.session_id WHERE u.engine=? AND u.event_key=?",
            (ps.engine, e.event_key),
        ).fetchone()
        if owner and (owner["started_at"] or "9999", owner["source_session_id"]) > my_rank:
            conn.execute("UPDATE usage_events SET session_id=? WHERE id=?", (session_id, owner["id"]))
            dirty.add(owner["session_id"])
        else:
            report.duplicate_events_skipped += 1


def _refresh_aggregates(conn, session_id: int, extra_warnings: Optional[list] = None):
    agg = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(input_tokens),0) i, COALESCE(SUM(cache_read_tokens),0) cr, "
        "COALESCE(SUM(cache_creation_tokens),0) cc, COALESCE(SUM(output_tokens),0) o, "
        "COALESCE(SUM(reasoning_tokens),0) r FROM usage_events WHERE session_id=?",
        (session_id,),
    ).fetchone()
    models = conn.execute(
        "SELECT model_id, SUM(output_tokens) o, COUNT(*) n FROM usage_events "
        "WHERE session_id=? AND model_id IS NOT NULL GROUP BY pricing_key "
        "ORDER BY o DESC, n DESC",
        (session_id,),
    ).fetchall()
    dominant = models[0]["model_id"] if models else None
    family, label = family_and_label(dominant)
    known = int(bool(models) and all(family_and_label(m["model_id"])[0] for m in models))
    warns = list(extra_warnings or [])
    if models and not known:
        unmapped = sorted({m["model_id"] for m in models if not family_and_label(m["model_id"])[0]})
        warns.append("unrecognized model id(s): " + ", ".join(unmapped))
    if len(models) > 1:
        warns.append(f"{len(models)} models used; per-call usage is priced per model")
    conn.execute(
        "UPDATE sessions SET usage_event_count=?, input_tokens=?, cache_read_input_tokens=?, "
        "cache_creation_input_tokens=?, output_tokens=?, reasoning_tokens=?, total_tokens=?, "
        "model_id=?, model_family=?, model_label=?, model_count=?, model_known=?, warning=? "
        "WHERE id=?",
        (
            agg["n"], agg["i"], agg["cr"], agg["cc"], agg["o"], agg["r"],
            agg["i"] + agg["cr"] + agg["cc"] + agg["o"],
            dominant, family, label, len(models), known, "; ".join(warns) or None, session_id,
        ),
    )


def _stored_warnings(conn, session_id: int) -> list:
    """Parser-level warnings survive an aggregate-only refresh (stored in metadata)."""
    row = conn.execute("SELECT raw_metadata_json FROM sessions WHERE id=?", (session_id,)).fetchone()
    try:
        return json.loads(row["raw_metadata_json"] or "{}").get("parse_warnings", [])
    except ValueError:
        return []


def _outranks(held_size, held_path, sf: SourceFile) -> bool:
    """True when the file already holding a session beats ``sf`` (larger, then smaller path)."""
    return (held_size or 0, sf.path) > (sf.size, held_path or "")


def _link_parents(conn):
    conn.execute(
        "UPDATE sessions SET parent_session_id = ("
        "  SELECT p.id FROM sessions p WHERE p.engine = sessions.engine "
        "  AND p.source_session_id = sessions.parent_source_session_id) "
        "WHERE parent_source_session_id IS NOT NULL"
    )


def ingest(
    conn,
    roots: Dict[str, str],
    engines: Optional[Iterable[str]] = None,
    full_rebuild: bool = False,
    dry_run: bool = False,
    log: Callable[[str], None] = lambda _m: None,
    commit_every: int = 200,
) -> Report:
    engines = list(engines or ADAPTERS)
    report = Report()
    ingested_at = _now()
    if full_rebuild:
        for eng in engines:
            conn.execute("DELETE FROM usage_events WHERE engine=?", (eng,))
            conn.execute("DELETE FROM sessions WHERE engine=?", (eng,))
            conn.execute("DELETE FROM ingest_files WHERE engine=?", (eng,))
    dirty: set = set()
    since_commit = 0
    for eng in engines:
        adapter = ADAPTERS[eng]
        root = roots.get(eng) or adapter.default_root()
        counts = report.engines[eng]
        if not os.path.isdir(root):
            report.note(eng, "missing_root", root, "source directory not found; nothing ingested")
            continue
        known = {
            r["path"]: r
            for r in conn.execute(
                "SELECT path, size, mtime_ns, session_count, skipped_count, status FROM ingest_files WHERE engine=?",
                (eng,),
            )
        }
        seen_paths = set()
        if hasattr(adapter, "skipped"):
            for path, reason in adapter.skipped(root):
                counts["discovered"] += 1
                counts["skipped"] += 1
                report.note(eng, "skipped", path, reason)
        for sf in adapter.discover(root):
            seen_paths.add(sf.path)
            prev = known.get(sf.path)
            if prev and prev["status"] == "ok" and prev["size"] == sf.size and prev["mtime_ns"] == sf.mtime_ns:
                counts["discovered"] += prev["session_count"]
                counts["skipped"] += prev["skipped_count"]
                counts["unchanged"] += prev["session_count"] - prev["skipped_count"]
                continue
            try:
                parsed = adapter.parse(sf)
            except Exception as exc:  # one bad file must not stop the run
                counts["discovered"] += 1
                counts["failed"] += 1
                report.note(eng, "failed", sf.path, f"{type(exc).__name__}: {exc}")
                conn.execute(
                    "INSERT OR REPLACE INTO ingest_files VALUES (?,?,?,?,?,?,?,?,?)",
                    (sf.path, eng, sf.size, sf.mtime_ns, 1, 0, "error", f"{type(exc).__name__}: {exc}", ingested_at),
                )
                continue
            total = len(parsed) or 1
            skipped_n = 0
            if not parsed:
                counts["discovered"] += 1
                counts["skipped"] += 1
                skipped_n = 1
                report.note(eng, "skipped", sf.path, "no session header in file")
            for ps in parsed:
                counts["discovered"] += 1
                if not ps.events and not ps.user_message_count and not ps.assistant_message_count:
                    counts["skipped"] += 1
                    skipped_n += 1
                    report.note(eng, "skipped", sf.path, "empty transcript (no messages, no usage)")
                    continue
                held = conn.execute(
                    "SELECT id, source_path, source_size FROM sessions WHERE engine=? AND source_session_id=?",
                    (ps.engine, ps.source_session_id),
                ).fetchone()
                if held and held["source_path"] != sf.path and _outranks(held["source_size"], held["source_path"], sf):
                    # The same session id also lives in a larger file (e.g. a moved session leaves a
                    # small stub behind). Keep that file's row; still claim any events unique to this one.
                    _claim_events(conn, held["id"], ps, report, dirty)
                    _refresh_aggregates(conn, held["id"], _stored_warnings(conn, held["id"]))
                    counts["skipped"] += 1
                    skipped_n += 1
                    report.note(eng, "skipped", sf.path,
                                f"secondary copy of session {ps.source_session_id} (canonical file: {held['source_path']})")
                    continue
                if ps.warnings:
                    ps.metadata["parse_warnings"] = ps.warnings
                sid, existed = _upsert_session(conn, ps, sf, ingested_at)
                before = _fingerprint(conn, sid) if existed else None
                _claim_events(conn, sid, ps, report, dirty)
                _refresh_aggregates(conn, sid, ps.warnings)
                dirty.discard(sid)
                after = _fingerprint(conn, sid)
                if not existed:
                    counts["inserted"] += 1
                elif before != after:
                    counts["updated"] += 1
                else:
                    counts["unchanged"] += 1
            conn.execute(
                "INSERT OR REPLACE INTO ingest_files VALUES (?,?,?,?,?,?,?,?,?)",
                (sf.path, eng, sf.size, sf.mtime_ns, total, skipped_n, "ok", None, ingested_at),
            )
            since_commit += 1
            if since_commit >= commit_every and not dry_run:
                conn.commit()
                log(f"  ... {eng}: {sum(counts[c] for c in ('inserted','updated','unchanged'))} sessions so far")
                since_commit = 0
        report.missing_source_files[eng] = len(set(known) - seen_paths)
    for sid in dirty:  # sessions that lost duplicated events to an earlier session
        _refresh_aggregates(conn, sid, _stored_warnings(conn, sid))
    _link_parents(conn)
    if dry_run:
        conn.rollback()
    else:
        conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('last_ingest_at', ?)", (ingested_at,)
        )
        conn.commit()
    return report
