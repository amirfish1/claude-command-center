"""Lazy `ccc uxlab` entry point."""
import argparse
import json
from pathlib import Path
import sys
from .core import make_plan, new_round_path, run_round


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ccc uxlab", description="Interactive model usability testers for disposable local apps (incurs model usage).")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run testers, or replay an earlier round")
    run.add_argument("url", nargs="?")
    run.add_argument("--again", type=Path, metavar="DIR")
    run.add_argument("--goal", action="append", default=None)
    run.add_argument("--people", type=int, default=None)
    devices = run.add_mutually_exclusive_group()
    devices.add_argument("--phone", action="store_true")
    devices.add_argument("--desktop", action="store_true")
    run.add_argument("--queue", metavar="NAME", help="explicitly file findings with wt add --no-dispatch")
    run.add_argument("--output", type=Path, metavar="DIR", help="new artifact directory (must not exist)")
    run.add_argument("--allow-host", action="append", default=None, metavar="HOST")
    run.add_argument("--allow-risky", action="store_true", help="relax action patterns; still fake data only")
    run.add_argument("--no-browser-sandbox", action="store_true", help="disable Chromium sandbox for a trusted disposable fixture only")
    run.add_argument("--model", default=None)
    run.add_argument("--timeout", type=int, default=None, help="seconds per tester (default 240)")
    run.add_argument("--steps", type=int, default=None, help="tool calls per tester (default 45)")
    args = parser.parse_args(argv)
    try:
        previous = []
        if args.again:
            if any((args.url, args.goal, args.people is not None, args.phone, args.desktop,
                    args.output, args.allow_host, args.allow_risky, args.model, args.no_browser_sandbox,
                    args.timeout is not None, args.steps is not None)):
                parser.error("--again reuses the exact plan; only --queue may be supplied alongside it")
            plan = json.loads((args.again / "plan.json").read_text())
            baseline = args.again / "replay-findings.json"
            previous = json.loads((baseline if baseline.exists() else args.again / "findings.json").read_text())
            folder = new_round_path(args.again)
        else:
            if not args.url:
                parser.error("URL or --again DIR is required")
            plan = make_plan(args.url, args.goal, args.people if args.people is not None else 4,
                             "phone" if args.phone else "desktop" if args.desktop else None,
                             args.allow_host or (), args.allow_risky, args.model or "gpt-6.1-sol",
                             args.timeout if args.timeout is not None else 240,
                             args.steps if args.steps is not None else 45, not args.no_browser_sandbox)
            folder = args.output or new_round_path(Path.cwd() / "uxlab-runs")
        print("UX lab: serial fresh browsers; model usage applies. Use disposable test apps only.", flush=True)
        outcome = run_round(plan, folder, previous=previous, queue=args.queue)
        print(f'Report: {folder / "report.html"}')
        filing_ok = all(f.get("ok") for f in outcome.get("filing", []))
        if not filing_ok:
            print("Queue filing failed; report preserved. See filing.json.", file=sys.stderr)
        return 0 if filing_ok and all(r.get("status") == "finished" for r in outcome["results"]) else 1
    except (ValueError, OSError) as exc:
        print(f"uxlab: {exc}", file=sys.stderr)
        return 1
