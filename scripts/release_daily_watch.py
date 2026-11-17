#!/usr/bin/env python3
"""Read-only evidence of a timer-triggered daily release, never a publisher."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess

UTC = timezone.utc
RELEASED = re.compile(r"^(\d{2}:\d{2}:\d{2}) RELEASED: (v\d+\.\d+\.\d+)$")


def timestamp(value):
    if not value or value in ("n/a", "0"):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return datetime.strptime(value, "%a %Y-%m-%d %H:%M:%S UTC").replace(tzinfo=UTC)


def result(status, reason, **evidence):
    return {"status": status, "real_data": status != "pending",
            "evidence": {"reason": reason, **evidence}}


def evaluate(unit, timer, since, logs, publication):
    """Only fresh unit success AND its containing published release can pass."""
    start = timestamp(unit.get("ExecMainStartTimestamp"))
    end = timestamp(unit.get("ExecMainExitTimestamp"))
    if unit.get("LoadState") != "loaded" or not start or start <= since:
        return result("pending", "No post-repair unit run")
    if unit.get("ActiveState") in ("active", "activating", "deactivating") or not end or end < start:
        return result("pending", "Post-repair run has not finished", started_at=start.isoformat())
    facts = {"started_at": start.isoformat(), "finished_at": end.isoformat(),
             "invocation_id": unit.get("InvocationID"), "result": unit.get("Result"),
             "exit_status": unit.get("ExecMainStatus")}
    if unit.get("Result") != "success" or unit.get("ExecMainStatus") != "0":
        return result("fail", "A fresh daily release run failed", **facts)
    trigger = timestamp(timer.get("LastTriggerUSec"))
    if not trigger or not 0 <= (start - trigger).total_seconds() <= 60:
        return result("pending", "Successful run is not tied to the natural timer", **facts)
    tags = []
    for day, text in logs.items():
        for line in text.splitlines():
            match = RELEASED.fullmatch(line.strip())
            if match and start <= timestamp(day + "T" + match[1] + "+00:00") <= end:
                tags.append(match[2])
    if not tags:
        return result("pending", "No publication receipt in this run (a waiver is not publication)", **facts)
    tag = tags[-1]
    receipt = publication(tag)
    published = timestamp(receipt.get("published_at"))
    if (receipt.get("draft") is not False or receipt.get("prerelease") is not False
            or receipt.get("tag_name") != tag or not published or not start <= published <= end
            or receipt.get("contains_required") is not True
            or not re.fullmatch(r"[a-f0-9]{40}", receipt.get("release_commit") or "")):
        return result("pending", "Published release does not yet prove this successful run contains the repair",
                      tag=tag, **facts)
    return result("pass", "Natural timer run succeeded and its containing release is published",
                  tag=tag, release_url=receipt.get("html_url"), published_at=receipt["published_at"],
                  release_commit=receipt.get("release_commit"), **facts)


def read_command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30,
                          env={**os.environ, "TZ": "UTC", "LC_ALL": "C"}).stdout


def properties(name):
    output = read_command("systemctl", "--user", "show", name,
                          "-p", "LoadState", "-p", "ActiveState", "-p", "Result",
                          "-p", "ExecMainStatus", "-p", "ExecMainStartTimestamp",
                          "-p", "ExecMainExitTimestamp", "-p", "InvocationID", "-p", "LastTriggerUSec")
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def github_publication(repo, required, tag):
    release = json.loads(read_command("gh", "api", f"repos/{repo}/releases/tags/{tag}"))
    comparison = json.loads(read_command("gh", "api", f"repos/{repo}/compare/{required}...{tag}"))
    return {**release, "contains_required": comparison.get("status") in ("ahead", "identical"),
            "release_commit": comparison.get("head_commit", {}).get("sha")}


def collect(args):
    unit = properties(args.unit)
    timer = properties(args.unit.removesuffix(".service") + ".timer")
    start = timestamp(unit.get("ExecMainStartTimestamp"))
    end = timestamp(unit.get("ExecMainExitTimestamp"))
    logs = {}
    if (start and end and start > args.since and start <= end
            and unit.get("Result") == "success" and unit.get("ExecMainStatus") == "0"
            and unit.get("ActiveState") not in ("active", "activating", "deactivating")):
        # The publisher has a three-hour service bound; avoid unbounded log scans.
        if end - start > timedelta(days=1):
            return result("pending", "Unit duration exceeds the bounded release log window")
        day = start.date()
        while day <= end.date():
            path = args.log_dir / (day.isoformat() + ".log")
            logs[day.isoformat()] = path.read_text() if path.exists() else ""
            day += timedelta(days=1)
    return evaluate(unit, timer, args.since, logs,
                    lambda tag: github_publication(args.repo, args.required_commit, tag))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, type=timestamp)
    parser.add_argument("--required-commit", required=True)
    parser.add_argument("--repo", default="amirfish1/claude-command-center")
    parser.add_argument("--unit", default="ccc-release-daily.service")
    parser.add_argument("--log-dir", type=Path, default=Path.home() / ".local/state/ccc-release")
    args = parser.parse_args()
    if (args.since is None or not re.fullmatch(r"[a-f0-9]{40}", args.required_commit)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+\.service", args.unit)):
        parser.error("expected a full commit SHA, owner/repo, and service name")
    try:
        report = collect(args)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report = result("pending", "Evidence unavailable; no successful publication inferred",
                        error_type=type(error).__name__)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
