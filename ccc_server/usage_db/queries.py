"""Read-only queries over the usage DB. Everything reads the normalized tables/views."""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from typing import Optional

_PERIODS = {
    "day": "substr(c.ts, 1, 10)",
    "week": "date(substr(c.ts, 1, 10), 'weekday 0', '-6 days')",  # Monday of the ISO week
    "month": "substr(c.ts, 1, 7)",
}


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


def list_sessions(
    conn,
    engine=None,
    provider=None,
    model=None,
    project=None,
    since=None,
    until=None,
    subagents: Optional[bool] = False,
    order="started_at DESC",
    limit=50,
):
    """Session list with cost. ``subagents``: False = top-level only, True = sub-agents only, None = both."""
    where, args = [], []
    if engine:
        where.append("s.engine = ?"); args.append(engine)
    if provider:
        where.append("s.provider = ?"); args.append(provider)
    if model:
        where.append("(s.model_id LIKE ? OR s.model_label LIKE ?)"); args += [f"%{model}%"] * 2
    if project:
        where.append("s.project_name LIKE ?"); args.append(f"%{project}%")
    if since:
        where.append("s.started_at >= ?"); args.append(since)
    if until:
        where.append("s.started_at < ?"); args.append(until)
    if subagents is not None:
        where.append("s.is_subagent = ?"); args.append(int(subagents))
    allowed = {"started_at": "s.started_at", "total_tokens": "s.total_tokens", "cost_usd": "c.cost_usd",
               "message_count": "s.message_count", "duration_seconds": "s.duration_seconds"}
    col, _, direction = order.partition(" ")
    if col not in allowed or direction.upper() not in ("", "ASC", "DESC"):
        raise ValueError(f"bad order: {order!r}")
    col = allowed[col]
    sql = (
        "SELECT s.id, s.engine, s.provider, s.source_session_id, s.is_subagent, s.model_id, s.model_label, "
        "s.model_count, s.project_name, s.started_at, s.last_activity_at, s.message_count, "
        "s.duration_seconds, s.input_tokens, s.cache_read_input_tokens, s.cache_creation_input_tokens, "
        "s.output_tokens, s.total_tokens, s.compaction_count, s.usage_complete, s.warning, "
        "c.cost_usd, c.cost_usd_priced, c.unpriced_event_count "
        "FROM sessions s JOIN session_costs c ON c.session_id = s.id"
        + (" WHERE " + " AND ".join(where) if where else "")
        + f" ORDER BY {col} {direction or 'DESC'} LIMIT ?"
    )
    return _rows(conn.execute(sql, (*args, int(limit))))


def summarize(conn, by="month", since=None, engine=None):
    """Tokens and API-list-price cost per ``day|week|month`` (UTC) and engine/model."""
    if by == "engine":
        sql = "SELECT * FROM cost_by_engine_model ORDER BY engine, cost_usd_priced DESC"
        return _rows(conn.execute(sql))
    if by not in _PERIODS:
        raise ValueError("by must be day, week, month or engine")
    where, args = ["c.ts IS NOT NULL"], []
    if since:
        where.append("c.ts >= ?"); args.append(since)
    if engine:
        where.append("c.engine = ?"); args.append(engine)
    sql = (
        f"SELECT {_PERIODS[by]} AS period, c.engine, COUNT(*) AS calls, "
        "SUM(c.input_tokens) AS input_tokens, SUM(c.cache_read_tokens) AS cache_read_tokens, "
        "SUM(c.cache_creation_tokens) AS cache_creation_tokens, SUM(c.output_tokens) AS output_tokens, "
        "SUM(c.input_tokens + c.cache_read_tokens + c.cache_creation_tokens + c.output_tokens) AS total_tokens, "
        "SUM(c.cost_usd) AS cost_usd_priced, "
        "SUM(CASE WHEN c.cost_usd IS NULL THEN 1 ELSE 0 END) AS unpriced_calls, "
        "SUM(c.cache_read_savings_usd) AS cache_read_savings_usd "
        "FROM event_costs c WHERE " + " AND ".join(where) + " GROUP BY period, c.engine ORDER BY period DESC, c.engine"
    )
    return _rows(conn.execute(sql, args))


def engine_activity(conn):
    """First/last billed call per engine, so 'stopped on date X' is read from the data."""
    return _rows(
        conn.execute(
            "SELECT engine, MIN(ts) AS first_call, MAX(ts) AS last_call, COUNT(*) AS calls "
            "FROM usage_events WHERE ts IS NOT NULL GROUP BY engine ORDER BY engine"
        )
    )


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def run_rate(conn, engine=None, as_of: Optional[str] = None):
    """Trailing-30-day API-equivalent cost and a month-to-date projection.

    ``as_of`` defaults to now (UTC). An engine with no calls in the window reports
    0 calls and cost 0.0 (data-backed zero); ``unpriced_calls`` > 0 means the cost
    is a lower bound.
    """
    now = _parse(as_of) if as_of else datetime.now(timezone.utc)
    start30 = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    elapsed_days = max((now - month_start).total_seconds() / 86400.0, 1e-9)
    end = now.strftime("%Y-%m-%dT%H:%M:%S")
    out = []
    engines = [engine] if engine else [r["engine"] for r in conn.execute("SELECT DISTINCT engine FROM sessions ORDER BY 1")]
    for eng in engines:
        def window(lo):
            r = conn.execute(
                "SELECT COUNT(*) calls, COALESCE(SUM(cost_usd),0) cost, "
                "COALESCE(SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END),0) unpriced "
                "FROM event_costs WHERE engine=? AND ts >= ? AND ts <= ?",
                (eng, lo, end + "Z"),
            ).fetchone()
            return r
        t30 = window(start30)
        mtd = window(month_start.strftime("%Y-%m-%dT%H:%M:%S"))
        out.append(
            {
                "engine": eng,
                "as_of": end + "Z",
                "trailing_30d_calls": t30["calls"],
                "trailing_30d_usd": round(t30["cost"], 2),
                "trailing_30d_unpriced_calls": t30["unpriced"],
                "month_to_date_usd": round(mtd["cost"], 2),
                "month_projected_usd": round(mtd["cost"] / elapsed_days * days_in_month, 2),
            }
        )
    return out


def active_plan_fee(conn, engine: str) -> Optional[float]:
    r = conn.execute(
        "SELECT SUM(monthly_fee) f, COUNT(*) n FROM subscription_plans WHERE engine=? AND active_to IS NULL",
        (engine,),
    ).fetchone()
    return r["f"] if r["n"] else None


def break_even(conn, extra_fee: float, engine="claude_code", as_of=None):
    """Compare a candidate extra subscription against API-equivalent usage.

    Subscription fees and API-equivalent cost stay separate numbers. The only
    derived figures are ratios of the two: what one subscription dollar
    currently 'covers' in list-price terms, and the share of the current run rate
    an extra subscription would have to absorb to pay for itself.
    """
    rr = run_rate(conn, engine=engine, as_of=as_of)
    rr = rr[0] if rr else {"trailing_30d_usd": 0.0, "trailing_30d_unpriced_calls": 0}
    api = rr["trailing_30d_usd"]
    current = active_plan_fee(conn, engine)
    res = {
        "engine": engine,
        "api_equivalent_trailing_30d_usd": api,
        "unpriced_calls_in_window": rr["trailing_30d_unpriced_calls"],
        "current_subscription_fees_usd": current,
        "candidate_extra_subscription_usd": extra_fee,
        "api_equivalent_per_current_subscription_dollar": (round(api / current, 2) if current else None),
        "extra_subscription_pays_for_itself_if_it_absorbs_usd": extra_fee,
        "as_share_of_trailing_30d_api_equivalent": (round(extra_fee / api, 3) if api else None),
    }
    if current is None:
        res["warning"] = "no active subscription plan configured for this engine (use 'plans add')"
    return res
