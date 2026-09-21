"""Rate-table management. Rates are data (``rates.json``), never inline in SQL rows."""

from __future__ import annotations

import json
import os
from typing import Optional

PACKAGED_RATES = os.path.join(os.path.dirname(__file__), "rates.json")


def load_rates(conn, path: Optional[str] = None) -> int:
    """Upsert rate rows from a JSON file (default: the packaged ``rates.json``).

    Rows are keyed by ``(pricing_key, effective_from)``, so re-loading is
    idempotent and a new effective date adds history instead of overwriting.
    """
    path = path or PACKAGED_RATES
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    n = 0
    for r in doc.get("rates", []):
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


def ensure_rates(conn) -> None:
    """Seed the packaged rates the first time (empty table only)."""
    if conn.execute("SELECT COUNT(*) FROM price_rates").fetchone()[0] == 0 and os.path.exists(PACKAGED_RATES):
        load_rates(conn)
