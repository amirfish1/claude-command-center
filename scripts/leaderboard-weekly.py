#!/usr/bin/env python3
"""Weekly free-model leaderboard refresh: rerun the eval, stage the page.

    python3 scripts/leaderboard-weekly.py run [--staging DIR] [--models ID ...]
    python3 scripts/leaderboard-weekly.py health [--staging DIR] [--max-age-days N]

``run`` races the local free router's models through the five benchmark
tasks (the same race as the dashboard's "Run the race", but it never changes
the router's default model), then exports the privacy-checked snapshot and a
copy of the page into the staging dir (default ``~/.ccc/leaderboard``) and
records the outcome in ``last-run.json`` there.

Nothing is committed, pushed, uploaded or deployed. Publishing stays a
reviewed, manual step: ``scripts/publish-leaderboard.sh --source-dir DIR``.

``health`` exits non-zero unless the last run succeeded within the window,
so a monitor can turn a silent stall into a ticket.

Exit codes: 0 ok, 1 unhealthy (health), 2 usage, 3 no router, 4 eval or
export failed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STAGING = Path.home() / ".ccc" / "leaderboard"
STATUS_FILE = "last-run.json"
EXIT_NO_ROUTER = 3
EXIT_FAILED = 4

sys.path.insert(0, str(REPO_ROOT))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_exporter():
    path = REPO_ROOT / "scripts" / "export-leaderboard.py"
    spec = importlib.util.spec_from_file_location("ccc_export_leaderboard", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_status(staging: Path, status: dict) -> None:
    staging.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=staging, prefix=".last-run.", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(status, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, staging / STATUS_FILE)


def run(staging: Path, models=None, log=print) -> int:
    from ccc_server import free_eval  # noqa: PLC0415 - after sys.path setup

    started = _now().isoformat(timespec="seconds")

    def finish(code, status, **fields):
        _write_status(staging, {"status": status, "started_at": started,
                                "ended_at": _now().isoformat(timespec="seconds"), **fields})
        return code

    cfg = free_eval.router_config(probe=True, force=True)
    if not cfg:
        log("leaderboard: no free router on this machine; nothing was run.")
        return finish(EXIT_NO_ROUTER, "no_router",
                      error="The free router is not set up on this machine.")
    try:
        result = free_eval.run_eval(cfg, models or None, log=log, pin=False)
    except free_eval.EvalError as e:
        log(f"leaderboard: eval failed at {e.step}: {e}")
        return finish(EXIT_FAILED, "eval_error", step=e.step, error=str(e))
    except Exception as e:  # a crashed race must still leave a status behind
        log(f"leaderboard: eval crashed: {str(e)[:200]}")
        return finish(EXIT_FAILED, "eval_error", step="crash", error=str(e)[:300])

    exporter = _load_exporter()
    try:
        payload = exporter.export(exporter.default_store_path(), staging / "data.json")
        shutil.copyfile(REPO_ROOT / "site" / "leaderboard" / "index.html", staging / "index.html")
    except (OSError, ValueError) as e:
        log(f"leaderboard: export failed: {e}")
        return finish(EXIT_FAILED, "export_error", error=str(e)[:300])
    public = len(payload.get("models", []))
    log(f"leaderboard: raced {result['evaluated']} model(s); {public} in the public snapshot.")
    log(f"leaderboard: staged in {staging}. Review, then: "
        f"scripts/publish-leaderboard.sh --source-dir {staging}")
    return finish(0, "ok", evaluated=result["evaluated"], public_models=public,
                  best=payload.get("best"))


def health(staging: Path, max_age_days: float, now=None) -> tuple[bool, str]:
    now = now or _now()
    try:
        status = json.loads((staging / STATUS_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, f"no run recorded yet ({staging / STATUS_FILE} is missing)"
    except (OSError, ValueError):
        return False, f"{staging / STATUS_FILE} is unreadable"
    try:
        ended = datetime.fromisoformat(str(status.get("ended_at")))
    except ValueError:
        return False, "last run has no valid ended_at"
    age = now - ended
    if status.get("status") != "ok":
        return False, f"last run {status.get('status')}: {status.get('error') or 'unknown error'}"
    if age > timedelta(days=max_age_days):
        return False, f"last successful run is {age.days} days old (limit {max_age_days:g})"
    return True, (f"ok: {status.get('public_models', 0)} public model(s), "
                  f"best {status.get('best') or 'none'}, {ended.date()}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="rerun the eval and stage the page")
    run_p.add_argument("--staging", type=Path, default=DEFAULT_STAGING)
    run_p.add_argument("--models", nargs="*", help="only race these model ids")
    health_p = sub.add_parser("health", help="exit 1 unless the last run is ok and recent")
    health_p.add_argument("--staging", type=Path, default=DEFAULT_STAGING)
    health_p.add_argument("--max-age-days", type=float, default=8.0)
    args = parser.parse_args(argv)
    if args.cmd == "run":
        return run(args.staging.expanduser(), args.models)
    ok, message = health(args.staging.expanduser(), args.max_age_days)
    print(f"leaderboard health: {message}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
