#!/usr/bin/env python3
"""Run the free-model leaderboard suite and write results JSON + markdown.

15 fixed coding tasks (ccc_server/leaderboard_suite.py) x every $0 model on
the requested backends. Paid models are never called: OpenRouter rows must
list a zero price, and OpenRouter requests also cap the price at 0. When an
OpenRouter key is used, its account usage is read before and after the run
and the difference is recorded as ``cost_usd``.

Usage:
    python3 scripts/run-leaderboard-eval.py [--backends router,openrouter,github]
        [--models ID,...] [--max-models N] [--tasks ID,...]
        [--out-dir docs/leaderboard] [--list]

Needs OPENROUTER_API_KEY for openrouter and a GitHub token (GITHUB_TOKEN or
``gh auth login``) for github. Results go to <out-dir>/latest.json and
latest.md. Nothing is published.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ccc_server import leaderboard_suite as suite  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backends", default="router,openrouter,github")
    parser.add_argument("--models", default="", help="comma-separated model ids to keep")
    parser.add_argument("--max-models", type=int, default=0, help="per backend; 0 = all")
    parser.add_argument("--tasks", default="", help="comma-separated task ids to keep")
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "docs" / "leaderboard"))
    parser.add_argument("--list", action="store_true", help="print tasks and models, run nothing")
    parser.add_argument("--rerender", action="store_true",
                        help="rebuild latest.md (and summaries) from latest.json, run nothing")
    args = parser.parse_args(argv)

    if args.rerender:
        out_dir = Path(args.out_dir)
        results = json.loads((out_dir / "latest.json").read_text(encoding="utf-8"))
        results["models"] = suite.rank([suite.summarize(m["backend"], m["model"], m["per_task"])
                                        for m in results["models"]])
        (out_dir / "latest.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        (out_dir / "latest.md").write_text(suite.render_markdown(results), encoding="utf-8")
        print(f"re-rendered {out_dir / 'latest.md'}")
        return 0

    lock = threading.Lock()

    def log(line):
        with lock:
            print(line, flush=True)

    wanted_tasks = {t for t in args.tasks.split(",") if t}
    if wanted_tasks:
        original = suite.suite_tasks
        suite.suite_tasks = lambda: [t for t in original() if t["id"] in wanted_tasks]
    tasks = suite.suite_tasks()
    if not tasks:
        parser.error("no tasks match --tasks")

    names = [b.strip() for b in args.backends.split(",") if b.strip()]
    skipped = []
    backends = suite.make_backends(names, log=lambda line: (log(line), skipped.append(line)))
    wanted_models = {m for m in args.models.split(",") if m}
    for backend in backends:
        models = backend["models"]
        if wanted_models:
            models = [m for m in models if m in wanted_models]
        if args.max_models:
            models = models[: args.max_models]
        backend["models"] = models

    if args.list:
        for task in tasks:
            print(f"task  {task['id']}: {task['title']}")
        for backend in backends:
            for model in backend["models"]:
                print(f"model {backend['name']}: {model}")
        return 0
    if not any(b["models"] for b in backends):
        log("no $0 models reachable; nothing to run")
        return 1

    cost_before = {b["name"]: b["cost_probe"]() for b in backends if b["cost_probe"]}
    run_at = suite._now_iso()

    def run_backend(backend):
        # Models on one backend share its rate limit, so run them in sequence.
        out = []
        for model in backend["models"]:
            log(f"{backend['name']}: {model}")
            out.append(suite.run_model(backend, model, log=log))
        return out

    with ThreadPoolExecutor(max_workers=max(1, len(backends))) as pool:
        models = [m for chunk in pool.map(run_backend, backends) for m in chunk]

    cost = 0.0
    for backend in backends:
        if backend["cost_probe"] and cost_before.get(backend["name"]) is not None:
            after = backend["cost_probe"]()
            if after is not None:
                cost += max(0.0, after - cost_before[backend["name"]])
    results = {
        "suite_version": suite.SUITE_VERSION,
        "run_at": run_at,
        "finished_at": suite._now_iso(),
        "cost_usd": round(cost, 6),
        "tasks": [{"id": t["id"], "title": t["title"]} for t in tasks],
        "backends": [{"name": b["name"], "models": len(b["models"])} for b in backends],
        "skipped": skipped,
        "models": suite.rank(models),
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    (out_dir / "latest.md").write_text(suite.render_markdown(results), encoding="utf-8")
    log(f"wrote {out_dir / 'latest.json'} and latest.md · cost ${cost:.4f}")
    if cost > 0:
        log("WARNING: account usage went up during the run; check the provider dashboard")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
