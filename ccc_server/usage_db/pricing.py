"""Rate-table management. Rates are data (``rates.json``), never inline in SQL rows."""

from __future__ import annotations

import json
import os
from typing import Optional

PACKAGED_RATES = os.path.join(os.path.dirname(__file__), "rates.json")


def load_rates(conn, path: Optional[str] = None, *, only_missing: bool = False) -> int:
    """Upsert rate rows from a JSON file (default: the packaged ``rates.json``).

    Rows are keyed by ``(pricing_key, effective_from)``, so re-loading is
    idempotent and a new effective date adds history instead of overwriting.
    ``only_missing`` adds newly packaged models without replacing custom rates.
    """
    path = path or PACKAGED_RATES
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    n = 0
    for r in doc.get("rates", []):
        if only_missing and conn.execute(
            "SELECT 1 FROM price_rates WHERE pricing_key=? LIMIT 1", (r["pricing_key"],)
        ).fetchone():
            continue
        conn.execute(
            "INSERT INTO price_rates (provider, pricing_key, effective_from, effective_to, currency, "
            "unit, input_rate, cache_read_rate, cache_write_5m_rate, cache_write_1h_rate, output_rate, "
            "source_note, verified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(pricing_key, effective_from) DO UPDATE SET "
            "provider=excluded.provider, effective_to=excluded.effective_to, currency=excluded.currency, "
            "unit=excluded.unit, input_rate=excluded.input_rate, cache_read_rate=excluded.cache_read_rate, "
            "cache_write_5m_rate=excluded.cache_write_5m_rate, cache_write_1h_rate=excluded.cache_write_1h_rate, "
            "output_rate=excluded.output_rate, source_note=excluded.source_note, verified_at=excluded.verified_at",
            (
                r.get("provider"), r["pricing_key"], r.get("effective_from", "1970-01-01"),
                r.get("effective_to"), r.get("currency", "USD"), r.get("unit", "per_1m_tokens"),
                r.get("input"), r.get("cache_read"), r.get("cache_write_5m"),
                r.get("cache_write_1h"), r.get("output"), r.get("source_note"), r.get("verified_at"),
            ),
        )
        n += 1
    conn.commit()
    return n


def default_discovered_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude", "command-center", "discovered-rates.json")


_RATE_FIELDS = (
    ("input", "input_rate"), ("cache_read", "cache_read_rate"),
    ("cache_write_5m", "cache_write_5m_rate"), ("cache_write_1h", "cache_write_1h_rate"),
    ("output", "output_rate"),
)


def load_discovered_rates(conn, path: Optional[str] = None, today: Optional[str] = None) -> int:
    """Merge vendor-published rates discovered by the dashboard's model refresh.

    ``discovered-rates.json`` holds ``rates.json``-shaped rows scraped from the
    vendors' pricing pages. A model with no rate row gets one effective from
    the epoch (a model has no usage before it shipped, so this prices all of
    its history). A model whose published price differs from its latest row
    gets a NEW row effective ``today``: past events keep the price they were
    billed at. Unchanged prices are left alone. Returns rows written.
    """
    path = path or default_discovered_path()
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return 0
    if today is None:
        from datetime import date
        today = date.today().isoformat()
    n = 0
    for r in doc.get("rates", []) if isinstance(doc, dict) else []:
        key = r.get("pricing_key")
        if not key or r.get("input") is None or r.get("output") is None:
            continue
        latest = conn.execute(
            "SELECT input_rate, cache_read_rate, cache_write_5m_rate, cache_write_1h_rate, output_rate "
            "FROM price_rates WHERE pricing_key=? AND currency='USD' ORDER BY effective_from DESC LIMIT 1",
            (key,),
        ).fetchone()
        if latest is None:
            effective_from = "1970-01-01"
        else:
            # Only compare fields the discovery actually published; a NULL on
            # our side (e.g. no 1h cache tier at OpenAI) must not force a row.
            changed = any(
                r.get(src) is not None and (latest[i] is None or abs(float(latest[i]) - float(r[src])) > 1e-9)
                for i, (src, _col) in enumerate(_RATE_FIELDS)
            )
            if not changed:
                continue
            effective_from = today
        conn.execute(
            "INSERT OR IGNORE INTO price_rates (provider, pricing_key, effective_from, currency, unit, "
            "input_rate, cache_read_rate, cache_write_5m_rate, cache_write_1h_rate, output_rate, "
            "source_note, verified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r.get("provider"), key, effective_from, "USD", "per_1m_tokens",
                r.get("input"), r.get("cache_read"), r.get("cache_write_5m"),
                r.get("cache_write_1h"), r.get("output"), r.get("source_note"), None,
            ),
        )
        n += conn.execute("SELECT changes()").fetchone()[0]
    conn.commit()
    return n


def ensure_rates(conn) -> None:
    """Add missing packaged models, preserving existing custom/history rows,
    then merge vendor-published rates the dashboard has discovered since."""
    if os.path.exists(PACKAGED_RATES):
        load_rates(conn, only_missing=True)
    load_discovered_rates(conn)
