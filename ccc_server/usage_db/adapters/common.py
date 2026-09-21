"""Helpers shared by the adapters."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterator, Optional, Tuple


def iso_from_ms(ms) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z"
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def norm_ts(value) -> Optional[str]:
    """Normalize a source timestamp to ``YYYY-MM-DDTHH:MM:SS.mmmZ`` (UTC)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return iso_from_ms(value if value > 1e11 else value * 1000)
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ts_min_max(cur: Tuple[Optional[str], Optional[str]], ts: Optional[str]):
    lo, hi = cur
    if not ts:
        return cur
    return (ts if lo is None or ts < lo else lo, ts if hi is None or ts > hi else hi)


def iter_json_lines(path: str, on_error) -> Iterator[Tuple[int, dict]]:
    """Yield ``(line_number, record)``; corrupt lines go to ``on_error(lineno, reason)``.

    Tolerates a torn final line (a session still being written) and invalid UTF-8.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                on_error(lineno, f"invalid JSON ({exc.msg})")
                continue
            if not isinstance(rec, dict):
                on_error(lineno, "record is not an object")
                continue
            yield lineno, rec


def as_int(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0
