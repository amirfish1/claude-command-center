"""Bounded SQLite reads without a Python/GIL round trip for every row."""
from __future__ import annotations

import json
import sqlite3


def fetch_rows(conn, sql, params, columns):
    """Read a bounded SELECT as tuples, preserving its order and scalar types.

    sqlite3 releases the GIL while stepping each result row. A busy Python
    thread can make a few hundred indexed rows take seconds. Aggregate one
    bounded chunk inside SQLite instead, then decode it in one Python call.
    Callers supply the selected column names and bound the query themselves.
    Older SQLite builds, BLOBs and REAL values retain the ordinary read.
    """
    names = ', '.join('"' + name.replace('"', '""') + '"' for name in columns)
    batch_sql = f'SELECT json_group_array(json_array({names})) FROM ({sql})'
    try:
        packed = conn.execute(batch_sql, params).fetchone()[0]
        rows = json.loads(packed)
        # SQLite JSON can round REAL values. Index columns are text/integers;
        # keep exact legacy values if another caller includes a float.
        if not any(isinstance(value, float) for row in rows for value in row):
            return [tuple(row) for row in rows]
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if not (('no such function:' in message and 'json_' in message)
                or 'json cannot hold blob values' in message):
            raise
    except json.JSONDecodeError:
        # Some SQLite versions emit non-standard JSON for non-finite reals.
        pass
    return [tuple(row) for row in conn.execute(sql, params)]
