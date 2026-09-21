"""Read-only queries over the usage DB. Everything reads the normalized tables/views."""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import fees

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


def _period_bounds(by, label):
    """``[start, end)`` dates of a period label from ``_PERIODS``."""
    d = fees.parse_day(label + "-01" if by == "month" else label)
    if by == "day":
        return d, d + timedelta(days=1)
    if by == "week":
        return d, d + timedelta(days=7)
    return d, (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def _attach_real(conn, row, start, end, since, as_of_day):
    """Add the real-cost columns to an engine-level row (never to a model slice)."""
    if since:
        start = max(start, fees.parse_day(since))
    end = min(end, as_of_day + timedelta(days=1))  # a period still running accrues through today
    noncache = (row["input_tokens"] or 0) + (row["cache_creation_tokens"] or 0) + (row["output_tokens"] or 0)
    fee = fees.fee_for_range(fees.load_plans(conn, row["engine"]), start, end) if start < end else None
    row.update(fees.real_metrics(fee, row["cost_usd_priced"], row["unpriced_calls"],
                                 row["total_tokens"], noncache))


_NO_REAL = fees.real_metrics(None, None, 0, 0, 0)


def _pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else None


def _aggregate(conn, period_expr, split_model, where, args):
    group = ([period_expr + " AS period"] if period_expr else []) + ["c.engine"] + (
        ["c.pricing_key AS model"] if split_model else [])
    keys = [g.split(" AS ")[-1] if " AS " in g else g for g in group]
    sql = (
        f"SELECT {', '.join(group)}, MIN(c.ts) AS first_ts, MAX(c.ts) AS last_ts, COUNT(*) AS calls, "
        "SUM(c.input_tokens) AS input_tokens, SUM(c.cache_read_tokens) AS cache_read_tokens, "
        "SUM(c.cache_creation_tokens) AS cache_creation_tokens, SUM(c.output_tokens) AS output_tokens, "
        "SUM(c.input_tokens + c.cache_read_tokens + c.cache_creation_tokens + c.output_tokens) AS total_tokens, "
        "SUM(c.cost_usd) AS cost_usd_priced, "
        "SUM(CASE WHEN c.cost_usd IS NULL THEN 1 ELSE 0 END) AS unpriced_calls "
        "FROM event_costs c WHERE " + " AND ".join(where)
        + f" GROUP BY {', '.join(keys)}"
        + (" ORDER BY period DESC, c.engine" + (", cost_usd_priced DESC" if split_model else "")
           if period_expr else " ORDER BY c.engine" + (", cost_usd_priced DESC" if split_model else ""))
    )
    rows = _rows(conn.execute(sql, args))
    for r in rows:
        r.update(_NO_REAL)  # filled in only on engine-level rows (see _attach_real)
        r["cache_read_pct"] = _pct(r["cache_read_tokens"], r["total_tokens"])
        r["unpriced_pct"] = _pct(r["unpriced_calls"], r["calls"])
    return rows


def summarize(conn, by="month", since=None, engine=None, model=None, split_model=False, as_of=None):
    """Tokens, API-list-price cost and what-you-really-pay per ``day|week|month`` (UTC) or ``engine``.

    Real-cost columns (``real_cost_usd``, ``real_usd_per_mtok``, ``real_usd_per_mtok_noncache``,
    ``list_to_real``) exist only on engine-level rows: the fee is per engine, so slicing by
    model (``split_model`` / ``model``) shows list price only and leaves them ``None``.
    ``by="engine"`` returns each engine's total row (model ``(all models)``) then its models.
    """
    if by != "engine" and by not in _PERIODS:
        raise ValueError("by must be day, week, month or engine")
    where, args = ["c.ts IS NOT NULL"], []
    if since:
        where.append("c.ts >= ?"); args.append(since)
    if engine:
        where.append("c.engine = ?"); args.append(engine)
    if model:
        where.append("(c.pricing_key LIKE ? OR c.model_id LIKE ?)"); args += [f"%{model}%"] * 2
    as_of_day = _parse(as_of).date() if as_of else datetime.now(timezone.utc).date()
    period_expr = None if by == "engine" else _PERIODS[by]
    sliced = bool(model)  # a model subset is not something the flat fee can be attributed to

    if by == "engine":
        models = _aggregate(conn, None, True, where, args)
        if sliced:
            return models
        out = []
        for tot in _aggregate(conn, None, False, where, args):
            tot["model"] = "(all models)"
            _attach_real(conn, tot, fees.parse_day(since or tot["first_ts"]), as_of_day + timedelta(days=1),
                         since, as_of_day)
            out.append(tot)
            out += [m for m in models if m["engine"] == tot["engine"]]
        return out

    rows = _aggregate(conn, period_expr, split_model, where, args)
    if not (split_model or sliced):
        for r in rows:
            start, end = _period_bounds(by, r["period"])
            _attach_real(conn, r, start, end, since, as_of_day)
    return rows


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
                "COALESCE(SUM(input_tokens+cache_read_tokens+cache_creation_tokens+output_tokens),0) tokens, "
                "COALESCE(SUM(input_tokens+cache_creation_tokens+output_tokens),0) noncache, "
                "COALESCE(SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END),0) unpriced "
                "FROM event_costs WHERE engine=? AND ts >= ? AND ts <= ?",
                (eng, lo, end + "Z"),
            ).fetchone()
            return r
        t30 = window(start30)
        mtd = window(month_start.strftime("%Y-%m-%dT%H:%M:%S"))
        plans = fees.load_plans(conn, eng)
        today = now.date()
        # 30 calendar dates ending today; month-to-date through today.
        real30 = fees.fee_for_range(plans, today - timedelta(days=29), today + timedelta(days=1))
        real_mtd = fees.fee_for_range(plans, month_start.date(), today + timedelta(days=1))
        m30 = fees.real_metrics(real30, t30["cost"], t30["unpriced"], t30["tokens"], t30["noncache"])
        mm = fees.real_metrics(real_mtd, mtd["cost"], mtd["unpriced"], mtd["tokens"], mtd["noncache"])
        out.append(
            {
                "engine": eng,
                "as_of": end + "Z",
                "trailing_30d_calls": t30["calls"],
                "trailing_30d_tokens": t30["tokens"],
                "trailing_30d_usd": round(t30["cost"], 2),
                "trailing_30d_unpriced_calls": t30["unpriced"],
                "trailing_30d_unpriced_pct": _pct(t30["unpriced"], t30["calls"]),
                "trailing_30d_real_usd": m30["real_cost_usd"],
                "trailing_30d_real_usd_per_mtok": m30["real_usd_per_mtok"],
                "trailing_30d_real_usd_per_mtok_noncache": m30["real_usd_per_mtok_noncache"],
                "trailing_30d_list_to_real": m30["list_to_real"],
                "month_to_date_usd": round(mtd["cost"], 2),
                "month_to_date_real_usd": mm["real_cost_usd"],
                "month_to_date_list_to_real": mm["list_to_real"],
                "month_projected_usd": round(mtd["cost"] / elapsed_days * days_in_month, 2),
            }
        )
    return out


def break_even(conn, extra_fee: float, engine="claude_code", as_of=None):
    """Compare a candidate extra subscription against API-equivalent usage.

    Fees and API-equivalent (list price) cost stay separate numbers. The derived
    figures are ratios of the two: ``list_to_real`` (how many list-price dollars
    each dollar you pay currently buys), and the share of the current run rate an
    extra subscription would have to absorb to pay for itself.
    """
    rr = run_rate(conn, engine=engine, as_of=as_of)
    rr = rr[0] if rr else {}
    api = rr.get("trailing_30d_usd", 0.0)
    day = _parse(as_of).date() if as_of else datetime.now(timezone.utc).date()
    current = fees.active_monthly_fee(conn, engine, day)
    res = {
        "engine": engine,
        "api_equivalent_trailing_30d_usd": api,
        "unpriced_calls_in_window": rr.get("trailing_30d_unpriced_calls", 0),
        "current_subscription_fees_usd": current,
        "real_cost_trailing_30d_usd": rr.get("trailing_30d_real_usd"),
        "real_usd_per_mtok": rr.get("trailing_30d_real_usd_per_mtok"),
        "real_usd_per_mtok_noncache": rr.get("trailing_30d_real_usd_per_mtok_noncache"),
        "list_to_real": rr.get("trailing_30d_list_to_real"),
        "candidate_extra_subscription_usd": extra_fee,
        "extra_subscription_pays_for_itself_if_it_absorbs_usd": extra_fee,
        "as_share_of_trailing_30d_api_equivalent": (round(extra_fee / api, 3) if api else None),
    }
    if current is None:
        res["warning"] = "no active subscription plan configured for this engine (use 'plans add')"
    return res
