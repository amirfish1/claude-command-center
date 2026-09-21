"""``throughput`` command line: ingest and query the usage DB."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile

from . import ingest as ingest_mod
from . import fees, pricing, queries, schema
from .adapters import ADAPTERS

ENGINE_ALIASES = {"claude": "claude_code", "claude-code": "claude_code", "claude_code": "claude_code",
                  "codex": "codex", "kimi": "kimi"}


def default_db_path() -> str:
    env = os.environ.get("CCC_THROUGHPUT_DB", "").strip()
    if env:
        return os.path.expanduser(env)
    return os.path.join(os.path.expanduser("~"), ".claude", "command-center", "usage", "throughput.sqlite3")


def _engine(value):
    if value is None:
        return None
    try:
        return ENGINE_ALIASES[value.lower()]
    except KeyError:
        raise argparse.ArgumentTypeError(f"unknown engine {value!r} (claude, codex, kimi)")


def _open(args, must_exist=False):
    path = args.db or default_db_path()
    if must_exist and not os.path.exists(path):
        sys.exit(f"no usage DB at {path}; run 'throughput ingest' first")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = schema.connect(path)
    schema.migrate(conn)
    return conn


# Table headers for the columns that mean "what you actually pay" vs "list price".
LABELS = {
    "cost_usd_priced": "list $", "cost_usd": "list $", "trailing_30d_usd": "list $ 30d",
    "real_cost_usd": "real $", "real_usd_per_mtok": "REAL /MTok",
    "real_usd_per_mtok_noncache": "REAL /MTok (non-cache)", "list_to_real": "LIST:REAL",
    "trailing_30d_real_usd": "real $ 30d", "trailing_30d_real_usd_per_mtok": "REAL /MTok 30d",
    "trailing_30d_real_usd_per_mtok_noncache": "REAL /MTok non-cache 30d",
    "trailing_30d_list_to_real": "LIST:REAL 30d", "month_to_date_usd": "list $ mtd",
    "month_to_date_real_usd": "real $ mtd", "month_to_date_list_to_real": "LIST:REAL mtd",
    "month_projected_usd": "list $ month proj", "cache_read_pct": "cache read %",
    "unpriced_pct": "unpriced %", "list_usd_per_mtok": "list /MTok", "list_share_pct": "% of list $",
    "trailing_30d_unpriced_pct": "unpriced % 30d", "monthly_fee": "monthly fee",
}
# Unpriced calls are only worth a column when they are a meaningful share of the calls.
UNPRICED_WARN_PCT = 15.0

_DOLLARS = {"cost_usd_priced", "cost_usd", "trailing_30d_usd", "real_cost_usd", "trailing_30d_real_usd",
            "month_to_date_usd", "month_to_date_real_usd", "month_projected_usd", "monthly_fee"}
_PER_MTOK = {"list_usd_per_mtok", "real_usd_per_mtok", "real_usd_per_mtok_noncache",
             "trailing_30d_real_usd_per_mtok", "trailing_30d_real_usd_per_mtok_noncache"}  # dollars per 1M tokens
_TOKENS = {"total_tokens", "trailing_30d_tokens"}
_MULTIPLIERS = {"list_to_real", "trailing_30d_list_to_real", "month_to_date_list_to_real"}
_PCT = {"cache_read_pct", "unpriced_pct", "list_share_pct", "trailing_30d_unpriced_pct"}


def _money(v):
    """``$XX``: whole dollars, with cents only below $10 (``$3.67``, ``$0.07``)."""
    if abs(v) >= 10:
        return f"${v:,.0f}"
    return "$" + f"{v:.2f}".rstrip("0").rstrip(".")


def _cents(dollars_per_mtok):
    """Per-1M-token price: cents below $1 (``2.31 cents``), dollars from $1 up (``$1.33``)."""
    if abs(dollars_per_mtok) >= 1:
        return f"${dollars_per_mtok:,.2f}"
    c = dollars_per_mtok * 100
    return f"{c:.1f} cents" if abs(c) >= 10 else f"{c:.2f} cents"


def _tokens(n):
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            x = n / div
            return f"{x:.0f}{suffix}" if x >= 100 else f"{x:.1f}{suffix}"
    return f"{n:,.0f}"


def _fmt(v, col=None, raw=False):
    if v is None:
        return "-"
    if not raw and isinstance(v, (int, float)) and not isinstance(v, bool):
        if col in _DOLLARS:
            return _money(v)
        if col in _PER_MTOK:
            return _cents(v)
        if col in _TOKENS:
            return _tokens(v)
        if col in _MULTIPLIERS:
            return f"{v:,.1f}x"
        if col in _PCT:
            return f"{v:.1f}"
    if isinstance(v, float):
        return f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


_NUMERIC = re.compile(r"-|\$?-?[\d,.]+( cents|[KMBx])?")


def print_table(rows, cols=None, out=sys.stdout, raw=False):
    if not rows:
        print("(no rows)", file=out)
        return
    cols = cols or list(rows[0].keys())
    body = [[_fmt(r.get(c), c, raw) for c in cols] for r in rows]
    heads = [c if raw else LABELS.get(c, c) for c in cols]
    widths = [max(len(h), *(len(b[i]) for b in body)) for i, h in enumerate(heads)]
    print("  ".join(h.ljust(w) for h, w in zip(heads, widths)), file=out)
    print("  ".join("-" * w for w in widths), file=out)
    for b in body:
        print("  ".join(v.rjust(w) if _NUMERIC.fullmatch(v) else v.ljust(w) for v, w in zip(b, widths)), file=out)


def cmd_ingest(args):
    roots = {"claude_code": args.claude_root, "codex": args.codex_root, "kimi": args.kimi_root}
    engines = args.engine or None
    if args.dry_run and not os.path.exists(args.db or default_db_path()):
        # Dry run against a fresh DB: use a throwaway file, never create the real one.
        tmp = tempfile.mkdtemp(prefix="throughput-dry-")
        args.db = os.path.join(tmp, "dry.sqlite3")
    conn = _open(args)
    pricing.ensure_rates(conn)
    log = (lambda m: print(m, file=sys.stderr)) if not args.json else (lambda m: None)
    rep = ingest_mod.ingest(conn, roots, engines, args.full_rebuild, args.dry_run, log)
    if args.json:
        print(json.dumps(rep.as_dict(), indent=2))
        return 0
    print(("DRY RUN (nothing written) - " if args.dry_run else "") + "ingest complete")
    rows = [{"engine": e, **c} for e, c in sorted(rep.engines.items())]
    print_table(rows, ["engine", "discovered", "inserted", "updated", "unchanged", "skipped", "failed"])
    print(f"usage events added: {rep.events_added:,}; duplicate events left with their first session: "
          f"{rep.duplicate_events_skipped:,}")
    for eng, n in sorted(rep.missing_source_files.items()):
        if n:
            print(f"note: {n} previously ingested {eng} source file(s) no longer exist (rows kept)")
    bad = [d for d in rep.details if d[1] in ("failed", "missing_root")]
    for eng, kind, path, reason in bad:
        print(f"  {kind.upper()} [{eng}] {path}: {reason}")
    if args.verbose:
        for eng, kind, path, reason in rep.details:
            if kind == "skipped":
                print(f"  skipped [{eng}] {path}: {reason}")
    return 1 if any(d[1] == "failed" for d in rep.details) else 0


def cmd_sessions(args):
    conn = _open(args, True)
    sub = {"exclude": False, "only": True, "include": None}[args.subagents]
    rows = queries.list_sessions(conn, args.engine, args.provider, args.model, args.project,
                                 args.since, args.until, sub, args.order, args.limit)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    for r in rows:
        r["started"] = (r["started_at"] or "")[:16]
        r["sid"] = r["source_session_id"][:14]
    print_table(rows, ["engine", "sid", "started", "model_label", "project_name", "message_count",
                       "total_tokens", "cost_usd", "unpriced_event_count"])
    return 0


def _with_unpriced(rows, cols, key):
    """Append the unpriced-% column, filled only for rows over the threshold, and only if any is."""
    over = [r for r in rows if (r.get(key) or 0) > UNPRICED_WARN_PCT]
    if not over:
        return rows, cols, False
    shown = [dict(r, **{key: r[key] if (r.get(key) or 0) > UNPRICED_WARN_PCT else None}) for r in rows]
    return shown, cols + [key], True


def _fee_notes(conn, rows):
    notes = []
    for eng in sorted({r["engine"] for r in rows}):
        if not fees.load_plans(conn, eng):
            notes.append(f"no fee configured for {eng}: its REAL columns are '-' (add one with 'plans add')")
        for name in fees.undated_plans(conn, eng):
            notes.append(f"plan '{name}' has no start date: its fee is applied to every period shown "
                         "(set one with 'plans add --name ... --since YYYY-MM-DD')")
    return notes


def cmd_summary(args):
    conn = _open(args, True)
    rows = queries.summarize(conn, args.by, args.since, args.engine, args.model, args.split_model,
                             args.by_family, args.as_of)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    model_view = bool(args.split_model or args.by_family or args.model)
    lead = ["engine", "model"] if args.by == "engine" else ["period", "engine"] + (["model"] if model_view else [])
    if model_view and args.by != "engine":
        # The fee is per engine, so a model view has no REAL columns; show what compares models instead.
        cols = lead + ["calls", "total_tokens", "cache_read_pct", "cost_usd_priced", "list_share_pct",
                       "list_usd_per_mtok"]
    else:
        cols = lead + ["calls", "total_tokens", "cache_read_pct", "cost_usd_priced", "real_cost_usd",
                       "list_usd_per_mtok", "real_usd_per_mtok", "list_to_real"]
    shown, cols, flagged = _with_unpriced(rows, cols, "unpriced_pct")
    print_table(shown, cols)
    print("\nlist $ = API list-price equivalent" + (
        f"; rows with unpriced % shown have more than {UNPRICED_WARN_PCT:.0f}% of calls on models with no "
        "(complete) price, so list $ and LIST:REAL there are lower bounds" if flagged else "") + ".")
    if not model_view or args.by == "engine":
        print("REAL = what you actually pay: your plan fee accrued daily over the period "
              "(monthly fee / days in month).\nREAL /MTok = real $ / all tokens, in cents (dollars from $1); LIST:REAL = list $ / "
              "real $. Days/weeks/months are UTC.")
    if model_view:
        print("Model views show list price only: the fee is per engine, so REAL columns are '-' on model rows. "
              "'% of list $' = the row's share of that engine's list cost in the period; "
              "'list /MTok' = list $ / all tokens, in cents (dollars from $1).")
    for n in _fee_notes(conn, rows):
        print("note: " + n)
    return 0


def cmd_activity(args):
    conn = _open(args, True)
    rows = queries.engine_activity(conn)
    print(json.dumps(rows, indent=2) if args.json else "", end="")
    if not args.json:
        print_table(rows)
    return 0


def cmd_runrate(args):
    conn = _open(args, True)
    rows = queries.run_rate(conn, args.engine, args.as_of)
    print(json.dumps(rows, indent=2) if args.json else "", end="")
    if not args.json:
        cols = ["engine", "trailing_30d_tokens", "trailing_30d_usd", "trailing_30d_real_usd",
                "trailing_30d_real_usd_per_mtok", "trailing_30d_list_to_real",
                "month_to_date_usd", "month_to_date_real_usd", "month_projected_usd"]
        shown, cols, flagged = _with_unpriced(rows, cols, "trailing_30d_unpriced_pct")
        print_table(shown, cols)
        if flagged:
            print(f"\nlist figures for rows with unpriced % shown are lower bounds (>{UNPRICED_WARN_PCT:.0f}% of calls unpriced).")
        for n in _fee_notes(conn, rows):
            print("note: " + n)
    return 0


def cmd_breakeven(args):
    conn = _open(args, True)
    res = queries.break_even(conn, args.fee, args.engine, args.as_of)
    print(json.dumps(res, indent=2))
    return 0


def _day(value):
    try:
        return fees.parse_day(value).isoformat() if value else None
    except ValueError:
        sys.exit(f"bad date {value!r}: use YYYY-MM-DD")


def cmd_plans(args):
    conn = _open(args)
    if args.action == "add":
        if args.currency.upper() != "USD":
            sys.exit("only USD plans are supported (rates and list-price costs are USD)")
        since, until = _day(args.since), _day(args.until)
        if since and until and until <= since:
            sys.exit("--until must be after --since (until is exclusive)")
        # Same name = update in place (this is how you set or fix a plan's dates).
        conn.execute("INSERT OR REPLACE INTO subscription_plans (name, engine, monthly_fee, currency, "
                     "active_from, active_to, note) VALUES (?,?,?,?,?,?,?)",
                     (args.name, args.engine, args.fee, "USD", since, until, args.note))
        conn.commit()
    print_table([dict(r) for r in conn.execute("SELECT * FROM subscription_plans ORDER BY engine, name")])
    print("\nactive_from is inclusive, active_to exclusive; blank = open-ended. "
          "Re-run 'plans add' with the same --name to change a plan.")
    return 0


def cmd_rates(args):
    conn = _open(args)
    if args.action == "load":
        n = pricing.load_rates(conn, args.file)
        print(f"loaded {n} rate row(s)")
    rows = [dict(r) for r in conn.execute(
        "SELECT pricing_key, effective_from, input_rate, cache_read_rate, cache_write_5m_rate, "
        "cache_write_1h_rate, output_rate, verified_at FROM price_rates ORDER BY pricing_key, effective_from")]
    print_table(rows)
    return 0


def cmd_sql(args):
    conn = _open(args, True)
    conn.execute("PRAGMA query_only = ON")
    print_table([dict(r) for r in conn.execute(args.query)], raw=True)
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="throughput", description=__doc__)
    p.add_argument("--db", help=f"SQLite path (default: {default_db_path()})")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest", help="read the session stores into the DB (incremental, idempotent)")
    s.add_argument("--engine", type=_engine, action="append", help="limit to an engine (repeatable)")
    s.add_argument("--dry-run", action="store_true", help="parse and report; write nothing")
    s.add_argument("--full-rebuild", action="store_true", help="drop the selected engines' rows and re-ingest")
    s.add_argument("--claude-root"); s.add_argument("--codex-root"); s.add_argument("--kimi-root")
    s.add_argument("--json", action="store_true"); s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("sessions", help="list sessions with tokens and cost")
    s.add_argument("--engine", type=_engine); s.add_argument("--provider"); s.add_argument("--model")
    s.add_argument("--project"); s.add_argument("--since"); s.add_argument("--until")
    s.add_argument("--subagents", choices=["exclude", "only", "include"], default="exclude")
    s.add_argument("--order", default="started_at DESC"); s.add_argument("--limit", type=int, default=30)
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_sessions)

    s = sub.add_parser("summary", help="tokens and cost by day/week/month/engine")
    s.add_argument("--by", choices=["day", "week", "month", "engine"], default="month")
    s.add_argument("--since"); s.add_argument("--engine", type=_engine)
    s.add_argument("--model", help="model name substring(s); comma-separate to compare side by side, "
                   "one row per name (e.g. sonnet,fable  or  fable-5-1)")
    s.add_argument("--split-model", action="store_true", help="one row per model version within each period")
    s.add_argument("--by-family", action="store_true",
                   help="like --split-model but Claude versions merge into Opus/Sonnet/Fable/Haiku")
    s.add_argument("--as-of", help="treat this UTC timestamp as 'now' (for the fee accrual)")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_summary)

    s = sub.add_parser("activity", help="first/last billed call per engine")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_activity)

    s = sub.add_parser("runrate", help="trailing-30-day and month-projected API-equivalent cost")
    s.add_argument("--engine", type=_engine); s.add_argument("--as-of")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_runrate)

    s = sub.add_parser("breakeven", help="second-subscription comparison (fee is an input)")
    s.add_argument("--fee", type=float, required=True, help="monthly fee of the candidate subscription")
    s.add_argument("--engine", type=_engine, default="claude_code"); s.add_argument("--as-of")
    s.set_defaults(fn=cmd_breakeven)

    s = sub.add_parser("plans", help="list/add subscription plans (fees are configuration)")
    s.add_argument("action", choices=["list", "add"], nargs="?", default="list")
    s.add_argument("--name"); s.add_argument("--engine", type=_engine); s.add_argument("--fee", type=float)
    s.add_argument("--currency", default="USD"); s.add_argument("--since", help="first day the fee applies (YYYY-MM-DD)")
    s.add_argument("--until", help="day the fee stops applying, exclusive (YYYY-MM-DD)"); s.add_argument("--note")
    s.set_defaults(fn=cmd_plans)

    s = sub.add_parser("rates", help="list rates or load a rates JSON file")
    s.add_argument("action", choices=["list", "load"], nargs="?", default="list")
    s.add_argument("--file"); s.set_defaults(fn=cmd_rates)

    s = sub.add_parser("sql", help="run a read-only SQL query")
    s.add_argument("query"); s.set_defaults(fn=cmd_sql)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "plans" and args.action == "add" and not (args.name and args.engine and args.fee is not None):
        sys.exit("plans add requires --name, --engine and --fee")
    try:
        return args.fn(args) or 0
    except sqlite3.Error as exc:
        sys.exit(f"database error: {exc}")


if __name__ == "__main__":
    sys.exit(main())
