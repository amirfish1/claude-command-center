#!/usr/bin/env python3
"""Export a reviewed local benchmark snapshot without private run details.

Reads ~/.ccc/free-eval.json or $CCC_FREE_EVAL_STATE. Only model IDs, checked
aggregate metrics, tool support, and UTC dates are copied. Task descriptions
are fixed public text. Display names, provider labels, prompts, errors,
router settings, and per-task output are never copied.

Usage:
    python3 scripts/export-leaderboard.py [--store PATH] [--out PATH] [--check]
    python3 scripts/export-leaderboard.py --validate PATH

Missing stores produce an honest empty snapshot. Corrupt stores fail without
replacing the previous export. This script never runs a benchmark, accesses
the network, schedules a job, commits, or publishes anything.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "site" / "leaderboard" / "data.json"
ENV_EVAL_STATE = "CCC_FREE_EVAL_STATE"  # same override ccc_server.free_eval uses

# Fixed public titles for the benchmark's five tasks. Stored titles and other
# free-form task text never enter the public export.
TASK_TITLES = {
    "edit_file": "Fix a typo in a file",
    "fix_test": "Make a failing test pass",
    "add_function": "Add a new function",
    "tool_read_write": "Read one file, write another",
    "multi_step": "Read two files, combine, write",
}
MODEL_FIELDS = {
    "id", "score", "pass_rate", "passed", "tasks", "median_request_ms",
    "supports_tools", "evaluated_at", "rank",
}
PAYLOAD_FIELDS = {"version", "generated_at", "last_eval_at", "tasks", "best", "models"}
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]*(?:/[A-Za-z0-9][A-Za-z0-9._:+-]*)*")


def default_store_path() -> Path:
    override = os.environ.get(ENV_EVAL_STATE)
    return Path(override) if override else Path.home() / ".ccc" / "free-eval.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso_date(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, OverflowError):
        return None


def _number(value) -> bool:
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _safe_model_id(value) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 160:
        return False
    lowered = value.lower()
    return bool(MODEL_ID.fullmatch(value)) and not (
        lowered.startswith(("sk-", "bearer", "users/", "home/", "private/", "tmp/"))
        or re.match(r"^[a-z]:", lowered)
        or any(segment in (".", "..") for segment in value.split("/"))
    )


def _read_json(path: Path, missing_ok=False) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if missing_ok:
            return {}
        raise ValueError("The JSON file is missing.") from None
    except (OSError, ValueError, UnicodeError):
        raise ValueError("The JSON file could not be read. The previous export is unchanged.") from None
    if not isinstance(raw, dict):
        raise ValueError("The JSON file must contain an object.")
    return raw


def _store_models(store: dict) -> dict:
    if not isinstance(store, dict):
        raise ValueError("The eval store must contain an object.")
    if "version" in store and (type(store["version"]) is not int or store["version"] != 1):
        raise ValueError("This eval store version is not supported.")
    tasks = store.get("tasks", list(TASK_TITLES))
    if not isinstance(tasks, list) or not all(isinstance(t, str) for t in tasks):
        raise ValueError("The store does not describe the five supported benchmark tasks.")
    if len(tasks) != 5 or set(tasks) != set(TASK_TITLES):
        raise ValueError("The store does not describe the five supported benchmark tasks.")
    models = store.get("models", {})
    if not isinstance(models, dict):
        raise ValueError("The eval store's models must be an object.")
    return models


def _task_list() -> list:
    return [{"id": tid, "title": title} for tid, title in TASK_TITLES.items()]


def _public_model_row(entry: dict) -> dict | None:
    """Exclude incomplete or invalid measurements instead of inventing zeros."""
    if not isinstance(entry, dict) or not _safe_model_id(entry.get("id")):
        return None
    date = _iso_date(entry.get("evaluated_at"))
    score = entry.get("score")
    rate = entry.get("pass_rate")
    passed = entry.get("passed")
    tasks = entry.get("tasks")
    median = entry.get("median_request_ms")
    if not date or not _number(score) or not 0 <= score <= 100:
        return None
    if type(tasks) is not int or tasks != 5 or type(passed) is not int or not 0 <= passed <= tasks:
        return None
    if not _number(rate) or not 0 <= rate <= 1 or abs(rate - passed / tasks) > 0.001:
        return None
    if median is not None and (type(median) is not int or median < 0):
        return None
    if type(entry.get("supports_tools")) is not bool:
        return None
    return {
        "id": entry["id"],
        "score": score,
        "pass_rate": rate,
        "passed": passed,
        "tasks": tasks,
        "median_request_ms": median,
        "supports_tools": entry["supports_tools"],
        "evaluated_at": date,
    }


def _rank(row: dict):
    # Same ordering as ccc_server.free_eval._ranked: score desc, then faster
    # median (a missing median sorts last), then id for stability.
    return (-row["score"], math.inf if row["median_request_ms"] is None else row["median_request_ms"], row["id"])


def _best(models: list) -> str | None:
    return next((m["id"] for m in models if m["supports_tools"] and m["passed"] > 0), None)


def build_payload(store: dict, generated_at: str | None = None) -> dict:
    """Copy saved metrics; do not rescore, rerun, or read any other local state."""
    by_id = {}
    for entry in _store_models(store).values():
        row = _public_model_row(entry)
        if row is not None:
            previous = by_id.get(row["id"])
            if previous is None or row["evaluated_at"] > previous["evaluated_at"]:
                by_id[row["id"]] = row
    models = sorted(by_id.values(), key=_rank)
    for index, row in enumerate(models, 1):
        row["rank"] = index
    stamp = _iso_date(generated_at) if generated_at is not None else _now_iso()
    if stamp is None:
        raise ValueError("The export date must be a timestamp with a timezone.")
    return {
        "version": 1,
        "generated_at": stamp,
        "last_eval_at": max((m["evaluated_at"] for m in models), default=None),
        "tasks": _task_list(),
        "best": _best(models),
        "models": models,
    }


def validate_payload(payload: dict) -> None:
    """Refuse unreviewed extra fields before a snapshot can be staged for Pages."""
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_FIELDS:
        raise ValueError("The public snapshot has unexpected fields.")
    if type(payload["version"]) is not int or payload["version"] != 1 or payload["tasks"] != _task_list():
        raise ValueError("The public snapshot schema is not supported.")
    if payload["generated_at"] is not None and _iso_date(payload["generated_at"]) != payload["generated_at"]:
        raise ValueError("The export date is not a UTC timestamp.")
    models = payload["models"]
    if not isinstance(models, list):
        raise ValueError("The public snapshot's models must be a list.")
    ids = set()
    for index, row in enumerate(models, 1):
        if not isinstance(row, dict) or set(row) != MODEL_FIELDS:
            raise ValueError("A public model row has unexpected fields.")
        cleaned = _public_model_row(row)
        if cleaned is None or any(cleaned[k] != row[k] for k in cleaned):
            raise ValueError("A public model row has invalid measurements.")
        if type(row["rank"]) is not int or row["rank"] != index or row["id"] in ids:
            raise ValueError("The public model ranks or IDs are invalid.")
        ids.add(row["id"])
    if models != sorted(models, key=_rank) or payload["best"] != _best(models):
        raise ValueError("The public model order does not match the saved scores.")
    if payload["last_eval_at"] != max((m["evaluated_at"] for m in models), default=None):
        raise ValueError("The last benchmark date does not match the model dates.")
    if models and payload["generated_at"] is None:
        raise ValueError("A populated snapshot needs an export date.")


def _write_payload(payload: dict, out_path: Path) -> None:
    validate_payload(payload)
    content = json.dumps(payload, indent=2, allow_nan=False) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=out_path.parent, prefix=".leaderboard-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        temporary.chmod(0o644)
        os.replace(temporary, out_path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def export(store_path: Path, out_path: Path) -> dict:
    if store_path.resolve() == out_path.resolve():
        raise ValueError("The export cannot overwrite the private eval store.")
    payload = build_payload(_read_json(store_path, missing_ok=True))
    _write_payload(payload, out_path)
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, help="eval store (default: $CCC_FREE_EVAL_STATE or ~/.ccc/free-eval.json)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output (default: site/leaderboard/data.json)")
    parser.add_argument("--check", action="store_true", help="print the public snapshot without writing")
    parser.add_argument("--validate", type=Path, help="validate an already exported public snapshot without writing")
    args = parser.parse_args(argv)
    try:
        if args.validate:
            if args.store is not None or args.check or args.out != DEFAULT_OUT:
                parser.error("--validate cannot be combined with export options")
            validate_payload(_read_json(args.validate))
            print("leaderboard: public snapshot validated")
            return 0
        store_path = args.store or default_store_path()
        if store_path.resolve() == args.out.resolve():
            raise ValueError("The export cannot overwrite the private eval store.")
        store = _read_json(store_path, missing_ok=True)
        payload = build_payload(store)
        if args.check:
            print(json.dumps(payload, indent=2, allow_nan=False))
            return 0
        _write_payload(payload, args.out)
        skipped = len(_store_models(store)) - len(payload["models"])
        print(f"leaderboard: wrote {len(payload['models'])} model(s); excluded {skipped} incomplete or invalid row(s).")
        if not payload["models"]:
            print("No scores published. Run a benchmark in CCC to collect real results.")
        print("Review model IDs and metrics before publishing. No results are uploaded automatically.")
        return 0
    except (ValueError, OSError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "The output could not be written."
        print(f"leaderboard: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
