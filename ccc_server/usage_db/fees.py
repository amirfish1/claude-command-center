"""What you actually pay: subscription fees accrued over a date range.

A fee is a flat monthly charge, so it only means something for a whole engine
over a period, never for a single model or session. Fees accrue daily
(``monthly_fee / days in that calendar month``), so a full calendar month costs
exactly the monthly fee and a partial period costs its share.

Plan dates: ``active_from`` inclusive, ``active_to`` exclusive, ``NULL`` = open.
A plan with no start date is assumed to have always been active.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Optional


def parse_day(value: str) -> date:
    """``YYYY-MM-DD`` (a longer ISO timestamp is truncated to its date)."""
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def load_plans(conn, engine: str):
    return [
        dict(r)
        for r in conn.execute(
            "SELECT name, monthly_fee, active_from, active_to FROM subscription_plans "
            "WHERE engine = ? ORDER BY name",
            (engine,),
        )
    ]


def fee_for_range(plans, start: date, end: date) -> Optional[float]:
    """Fees accrued over ``[start, end)``; ``None`` if no plan is active on any of those days."""
    total, covered = 0.0, False
    for p in plans:
        a = max(start, parse_day(p["active_from"])) if p["active_from"] else start
        b = min(end, parse_day(p["active_to"])) if p["active_to"] else end
        d = a
        while d < b:
            dim = calendar.monthrange(d.year, d.month)[1]
            seg_end = min(b, date(d.year, d.month, dim) + timedelta(days=1))
            total += p["monthly_fee"] * (seg_end - d).days / dim
            covered = True
            d = seg_end
    return total if covered else None


def active_monthly_fee(conn, engine: str, on: date) -> Optional[float]:
    """Sum of the monthly fees of plans active on ``on``; ``None`` if there are none."""
    active = [
        p for p in load_plans(conn, engine)
        if (not p["active_from"] or parse_day(p["active_from"]) <= on)
        and (not p["active_to"] or parse_day(p["active_to"]) > on)
    ]
    return sum(p["monthly_fee"] for p in active) if active else None


def undated_plans(conn, engine: str):
    """Plans with no start date: their fee is applied to every period shown."""
    return [p["name"] for p in load_plans(conn, engine) if not p["active_from"]]


def real_metrics(real_cost, list_usd, unpriced_calls, total_tokens, noncache_tokens) -> dict:
    """The 'what you really pay' figures for one engine over one period.

    ``list_to_real`` is a lower bound when ``unpriced_calls`` > 0 (list cost is
    then only the priced part). All ``None`` when no fee was active.
    """
    out = {"real_cost_usd": None, "real_usd_per_mtok": None,
           "real_usd_per_mtok_noncache": None, "list_to_real": None}
    if real_cost is None:
        return out
    out["real_cost_usd"] = round(real_cost, 2)
    if total_tokens:
        out["real_usd_per_mtok"] = round(real_cost * 1e6 / total_tokens, 5)
    if noncache_tokens:
        out["real_usd_per_mtok_noncache"] = round(real_cost * 1e6 / noncache_tokens, 4)
    if real_cost > 0 and list_usd is not None:
        out["list_to_real"] = round(list_usd / real_cost, 2)
    return out
