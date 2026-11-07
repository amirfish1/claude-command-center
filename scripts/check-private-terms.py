#!/usr/bin/env python3
"""Fail when pushed content contains a term from the maintainer's private list.

The list lives outside the repo, so this file names no term and reports only
"path:line (term #N)". With no list, as on a contributor's machine or in CI, it
prints one line and exits 0.

List: $CCC_PRIVATE_TERMS_FILE, else every ~/.config/ccc-private-terms/*.txt. One
term per line, matched case-insensitively as a literal; "#" lines are comments;
in "term => replacement" only the left side is a term.

CHANGELOG.md is skipped: released history is never hand-edited, and new entries
are checked here first as changelog.d/ snippets.
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

EXEMPT_PATHS = ("CHANGELOG.md",)


def list_files() -> list[str]:
    explicit = os.environ.get("CCC_PRIVATE_TERMS_FILE")
    if explicit:
        return [explicit] if os.path.isfile(explicit) else []
    return sorted(glob.glob(os.path.expanduser("~/.config/ccc-private-terms/*.txt")))


def load_terms(paths: list[str] | None = None) -> list[str]:
    terms: list[str] = []
    for path in list_files() if paths is None else paths:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                term = line.split("=>", 1)[0].strip()
                if term and term not in terms:
                    terms.append(term)
    return terms


def term_numbers(text: str, terms: list[str]) -> list[int]:
    low = text.lower()
    return [i for i, t in enumerate(terms, 1) if t.lower() in low]


def scan_tree(rev: str, terms: list[str]) -> list[str]:
    excludes = [f":(exclude){p}" for p in EXEMPT_PATHS]
    res = subprocess.run(
        ["git", "grep", "-n", "-I", "-i", "-F", "-f", "-", rev, "--", ".", *excludes],
        input="\n".join(terms) + "\n", capture_output=True, text=True, errors="replace",
    )
    if res.returncode not in (0, 1):
        raise RuntimeError(f"git grep failed with code {res.returncode}")
    hits = []
    for row in res.stdout.splitlines():
        parts = row.split(":", 3)  # <rev>:<path>:<line>:<text>
        if len(parts) == 4:
            nums = term_numbers(parts[3], terms)
            hits.append(f"{parts[1]}:{parts[2]} (term #{','.join(map(str, nums))})")
    names = subprocess.run(["git", "ls-tree", "-r", "--name-only", rev],
                           capture_output=True, text=True, check=True).stdout
    for name in names.splitlines():
        nums = term_numbers(name, terms)
        if nums:
            hits.append(f"file name {name} (term #{','.join(map(str, nums))})")
    return hits


def scan_messages(rev_args: list[str], terms: list[str]) -> list[str]:
    log = subprocess.run(["git", "log", "--format=%h%x00%B%x01", *rev_args],
                         capture_output=True, text=True, errors="replace", check=True).stdout
    hits = []
    for entry in log.split("\x01"):
        sha, _, body = entry.strip().partition("\x00")
        nums = term_numbers(body, terms)
        if sha and nums:
            hits.append(f"commit message {sha} (term #{','.join(map(str, nums))})")
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--rev", default="HEAD", help="tree to scan (default HEAD)")
    ap.add_argument("--log", nargs=argparse.REMAINDER, default=[],
                    help="git log revisions whose messages are also scanned")
    args = ap.parse_args(argv)
    terms = load_terms()
    if not terms:
        print("private-terms: no list on this machine, skipping")
        return 0
    hits = scan_tree(args.rev, terms)
    if args.log:
        hits += scan_messages(args.log, terms)
    if not hits:
        print(f"private-terms: clean ({len(terms)} terms)")
        return 0
    print(f"private-terms: {len(hits)} match(es) of the private list:")
    for hit in hits:
        print("  " + hit)
    print("Use neutral wording, or local-overrides.json for per-install behaviour.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
