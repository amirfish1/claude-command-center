"""``throughput`` command line: ingest and query the usage DB."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile

from . import ingest as ingest_mod
from . import pricing, queries, schema
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


def _fmt(v):
    if v is None:
        return "unknown" if False else "-"
    if isinstance(v, float):
        return f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def print_table(rows, cols=None, out=sys.stdout):
    if not rows:
        print("(no rows)", file=out)
        return
    cols = cols or list(rows[0].keys())
    body = [[_fmt(r.get(c)) for c in cols] for r in rows]
    widths = [max(len(c), *(len(b[i]) for b in body)) for i, c in enumerate(cols)]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)), file=out)
    print("  ".join("-" * w for w in widths), file=out)
    for b in body:
        print("  ".join(v.rjust(w) if v.replace(",", "").replace(".", "").replace("-", "").isdigit() else v.ljust(w)
                        for v, w in zip(b, widths)), file=out)


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


def cmd_summary(args):
    conn = _open(args, True)
    rows = queries.summarize(conn, args.by, args.since, args.engine)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    cols = (["engine", "model", "first_ts", "last_ts"] if args.by == "engine" else ["period", "engine"]) + [
        "calls", "total_tokens", "cache_read_tokens", "cost_usd_priced", "unpriced_calls", "cache_read_savings_usd"]
    print_table(rows, cols)
    print("\ncost = API list-price equivalent over priced calls; 'unpriced_calls' > 0 means a lower bound. "
          "Days/weeks/months are UTC.")
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
        print_table(rows)
    return 0


def cmd_breakeven(args):
    conn = _open(args, True)
    res = queries.break_even(conn, args.fee, args.engine, args.as_of)
    print(json.dumps(res, indent=2))
    return 0


def cmd_plans(args):
    conn = _open(args)
    if args.action == "add":
        conn.execute("INSERT OR REPLACE INTO subscription_plans (name, engine, monthly_fee, currency, "
                     "active_from, note) VALUES (?,?,?,?,?,?)",
                     (args.name, args.engine, args.fee, args.currency, args.since, args.note))
        conn.commit()
    print_table([dict(r) for r in conn.execute("SELECT * FROM subscription_plans ORDER BY engine, name")])
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
    print_table([dict(r) for r in conn.execute(args.query)])
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
    s.add_argument("--currency", default="USD"); s.add_argument("--since"); s.add_argument("--note")
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
