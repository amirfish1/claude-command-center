"""SQLite schema, migrations and cost views for the usage DB."""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

_TABLES = """
CREATE TABLE IF NOT EXISTS ingest_files (
    path            TEXT PRIMARY KEY,
    engine          TEXT NOT NULL,
    size            INTEGER NOT NULL,
    mtime_ns        INTEGER NOT NULL,
    session_count   INTEGER NOT NULL DEFAULT 0,   -- sessions parsed from the file (kept + skipped)
    skipped_count   INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'ok',   -- ok | error
    error           TEXT,
    ingested_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id                          INTEGER PRIMARY KEY,
    engine                      TEXT NOT NULL,     -- claude_code | codex | kimi
    source_session_id           TEXT NOT NULL,
    provider                    TEXT,
    parent_session_id           INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    parent_source_session_id    TEXT,
    is_subagent                 INTEGER NOT NULL DEFAULT 0,
    agent_label                 TEXT,
    -- Dominant model = the one with most output tokens; all models are in
    -- usage_events and model_count says whether the session mixed models.
    model_id                    TEXT,
    model_family                TEXT,
    model_label                 TEXT,
    model_count                 INTEGER NOT NULL DEFAULT 0,
    project_name                TEXT,
    working_directory           TEXT,
    git_branch                  TEXT,
    started_at                  TEXT,
    last_activity_at            TEXT,
    ended_at                    TEXT,              -- no engine records an explicit end; always NULL
    status                      TEXT NOT NULL DEFAULT 'unknown',  -- active | archived | unknown
    -- conversation_length = message_count (user + assistant messages) AND
    -- duration_seconds (last_activity_at - started_at). Both are stored.
    user_message_count          INTEGER NOT NULL DEFAULT 0,
    assistant_message_count     INTEGER NOT NULL DEFAULT 0,
    message_count               INTEGER NOT NULL DEFAULT 0,
    tool_call_count             INTEGER NOT NULL DEFAULT 0,
    duration_seconds            INTEGER,
    -- Token buckets are disjoint: total = input + cache_read + cache_creation + output.
    input_tokens                INTEGER NOT NULL DEFAULT 0,   -- fresh (uncached) input
    cache_read_input_tokens     INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens               INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens            INTEGER NOT NULL DEFAULT 0,   -- subset of output_tokens
    total_tokens                INTEGER NOT NULL DEFAULT 0,
    usage_event_count           INTEGER NOT NULL DEFAULT 0,
    compaction_count            INTEGER NOT NULL DEFAULT 0,
    source_path                 TEXT,
    source_format_version       TEXT,
    source_modified_at          TEXT,
    ingested_at                 TEXT NOT NULL,
    raw_metadata_json           TEXT,
    usage_complete              INTEGER NOT NULL DEFAULT 1,
    model_known                 INTEGER NOT NULL DEFAULT 0,
    warning                     TEXT,
    UNIQUE (engine, source_session_id)
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_engine ON sessions(engine, started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_session_id);

-- One row per billed model call. Needed for per-model pricing, mid-session
-- model changes and day/week/month bucketing; sessions rows are aggregates of it.
CREATE TABLE IF NOT EXISTS usage_events (
    id                       INTEGER PRIMARY KEY,
    session_id               INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    engine                   TEXT NOT NULL,
    event_key                TEXT NOT NULL,
    ts                       TEXT,
    model_id                 TEXT,
    pricing_key              TEXT,
    input_tokens             INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens        INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens    INTEGER NOT NULL DEFAULT 0,
    cache_creation_1h_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens            INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens         INTEGER NOT NULL DEFAULT 0,
    scope                    TEXT NOT NULL DEFAULT 'call',
    UNIQUE (engine, event_key)
);
CREATE INDEX IF NOT EXISTS idx_events_session ON usage_events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_ts ON usage_events(ts);

-- Versioned API list prices, USD per 1,000,000 tokens. Rows are data, not code:
-- see docs/usage-db.md for the update procedure. A NULL rate means "not
-- published / not applicable" and makes any event that uses it unpriced.
CREATE TABLE IF NOT EXISTS price_rates (
    id                  INTEGER PRIMARY KEY,
    provider            TEXT,
    pricing_key         TEXT NOT NULL,
    effective_from      TEXT NOT NULL,     -- inclusive, ISO date
    effective_to        TEXT,              -- exclusive, ISO date, NULL = open
    currency            TEXT NOT NULL DEFAULT 'USD',
    unit                TEXT NOT NULL DEFAULT 'per_1m_tokens',
    input_rate          REAL,
    cache_read_rate     REAL,
    cache_write_5m_rate REAL,              -- also used for engines with one cache-write price
    cache_write_1h_rate REAL,
    output_rate         REAL,
    source_note         TEXT,
    verified_at         TEXT,              -- NULL = carried over, not independently verified
    UNIQUE (pricing_key, effective_from)
);

-- Subscription fees are configuration supplied by the user, never inferred.
CREATE TABLE IF NOT EXISTS subscription_plans (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    engine          TEXT NOT NULL,
    monthly_fee     REAL NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'USD',
    active_from     TEXT,
    active_to       TEXT,
    note            TEXT
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_VIEWS = """
DROP VIEW IF EXISTS event_costs;
CREATE VIEW event_costs AS
SELECT
    e.id AS event_id, e.session_id, e.engine, e.ts, e.model_id, e.pricing_key,
    e.input_tokens, e.cache_read_tokens, e.cache_creation_tokens,
    e.cache_creation_1h_tokens, e.output_tokens,
    r.id AS rate_id,
    -- NULL (unknown) unless the rate row has every rate this event actually uses.
    CASE WHEN r.id IS NULL THEN NULL
         WHEN (e.input_tokens > 0 AND r.input_rate IS NULL)
           OR (e.cache_read_tokens > 0 AND r.cache_read_rate IS NULL)
           OR ((e.cache_creation_tokens - e.cache_creation_1h_tokens) > 0 AND r.cache_write_5m_rate IS NULL)
           OR (e.cache_creation_1h_tokens > 0 AND COALESCE(r.cache_write_1h_rate, r.cache_write_5m_rate) IS NULL)
           OR (e.output_tokens > 0 AND r.output_rate IS NULL) THEN NULL
         ELSE (
            e.input_tokens * COALESCE(r.input_rate, 0)
          + e.cache_read_tokens * COALESCE(r.cache_read_rate, 0)
          + (e.cache_creation_tokens - e.cache_creation_1h_tokens) * COALESCE(r.cache_write_5m_rate, 0)
          + e.cache_creation_1h_tokens * COALESCE(r.cache_write_1h_rate, r.cache_write_5m_rate, 0)
          + e.output_tokens * COALESCE(r.output_rate, 0)
         ) / 1000000.0
    END AS cost_usd,
    -- What the cache reads would have cost at the full input rate, minus what
    -- they did cost. NULL when the input or cache-read rate is unknown.
    CASE WHEN r.input_rate IS NULL OR r.cache_read_rate IS NULL THEN NULL
         ELSE e.cache_read_tokens * (r.input_rate - r.cache_read_rate) / 1000000.0
    END AS cache_read_savings_usd
FROM usage_events e
LEFT JOIN price_rates r ON r.id = (
    SELECT p.id FROM price_rates p
    WHERE p.pricing_key = e.pricing_key
      AND p.currency = 'USD'
      AND substr(COALESCE(e.ts, '9999'), 1, 10) >= p.effective_from
      AND (p.effective_to IS NULL OR substr(COALESCE(e.ts, '9999'), 1, 10) < p.effective_to)
    ORDER BY p.effective_from DESC LIMIT 1
);

DROP VIEW IF EXISTS session_costs;
CREATE VIEW session_costs AS
SELECT
    s.id AS session_id, s.engine, s.provider, s.source_session_id, s.model_id,
    s.model_label, s.project_name, s.is_subagent, s.started_at, s.last_activity_at,
    s.message_count, s.duration_seconds, s.total_tokens,
    COUNT(c.event_id) AS event_count,
    SUM(CASE WHEN c.cost_usd IS NULL THEN 1 ELSE 0 END) AS unpriced_event_count,
    -- Sum over priced events only; cost_complete says whether that is everything.
    SUM(c.cost_usd) AS cost_usd_priced,
    CASE WHEN COUNT(c.event_id) = 0 THEN NULL
         WHEN SUM(CASE WHEN c.cost_usd IS NULL THEN 1 ELSE 0 END) = 0 THEN 1 ELSE 0 END AS cost_complete,
    CASE WHEN COUNT(c.event_id) > 0
          AND SUM(CASE WHEN c.cost_usd IS NULL THEN 1 ELSE 0 END) = 0
         THEN SUM(c.cost_usd) END AS cost_usd,
    SUM(c.cache_read_savings_usd) AS cache_read_savings_usd,
    CASE WHEN s.message_count > 0 AND COUNT(c.event_id) > 0
          AND SUM(CASE WHEN c.cost_usd IS NULL THEN 1 ELSE 0 END) = 0
         THEN SUM(c.cost_usd) * 1.0 / s.message_count END AS cost_per_message_usd,
    CASE WHEN s.duration_seconds >= 60 AND COUNT(c.event_id) > 0
          AND SUM(CASE WHEN c.cost_usd IS NULL THEN 1 ELSE 0 END) = 0
         THEN SUM(c.cost_usd) * 3600.0 / s.duration_seconds END AS cost_per_elapsed_hour_usd
FROM sessions s
LEFT JOIN event_costs c ON c.session_id = s.id
GROUP BY s.id;

-- Period roll-ups are built from events (not sessions) so a session that spans
-- midnight, or mixes models, lands in the right bucket at the right rate.
DROP VIEW IF EXISTS cost_by_day;
CREATE VIEW cost_by_day AS
SELECT substr(ts, 1, 10) AS day, engine, pricing_key AS model,
       COUNT(*) AS calls,
       SUM(input_tokens) AS input_tokens, SUM(cache_read_tokens) AS cache_read_tokens,
       SUM(cache_creation_tokens) AS cache_creation_tokens, SUM(output_tokens) AS output_tokens,
       SUM(input_tokens + cache_read_tokens + cache_creation_tokens + output_tokens) AS total_tokens,
       SUM(cost_usd) AS cost_usd_priced,
       SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS unpriced_calls,
       SUM(cache_read_savings_usd) AS cache_read_savings_usd
FROM event_costs WHERE ts IS NOT NULL
GROUP BY day, engine, pricing_key;

DROP VIEW IF EXISTS cost_by_month;
CREATE VIEW cost_by_month AS
SELECT substr(day, 1, 7) AS month, engine, model,
       SUM(calls) AS calls, SUM(input_tokens) AS input_tokens,
       SUM(cache_read_tokens) AS cache_read_tokens,
       SUM(cache_creation_tokens) AS cache_creation_tokens,
       SUM(output_tokens) AS output_tokens, SUM(total_tokens) AS total_tokens,
       SUM(cost_usd_priced) AS cost_usd_priced, SUM(unpriced_calls) AS unpriced_calls,
       SUM(cache_read_savings_usd) AS cache_read_savings_usd
FROM cost_by_day GROUP BY month, engine, model;

DROP VIEW IF EXISTS cost_by_engine_model;
CREATE VIEW cost_by_engine_model AS
SELECT engine, pricing_key AS model, MIN(ts) AS first_ts, MAX(ts) AS last_ts,
       COUNT(*) AS calls,
       SUM(input_tokens) AS input_tokens, SUM(cache_read_tokens) AS cache_read_tokens,
       SUM(cache_creation_tokens) AS cache_creation_tokens, SUM(output_tokens) AS output_tokens,
       SUM(input_tokens + cache_read_tokens + cache_creation_tokens + output_tokens) AS total_tokens,
       SUM(cost_usd) AS cost_usd_priced,
       SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS unpriced_calls,
       SUM(cache_read_savings_usd) AS cache_read_savings_usd
FROM event_costs GROUP BY engine, pricing_key;
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Create/upgrade the schema. Views are always recreated (they hold no data)."""
    have = conn.execute("PRAGMA user_version").fetchone()[0]
    if have > SCHEMA_VERSION:
        raise RuntimeError(
            f"usage DB schema v{have} is newer than this code (v{SCHEMA_VERSION})"
        )
    conn.executescript(_TABLES)
    conn.executescript(_VIEWS)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
