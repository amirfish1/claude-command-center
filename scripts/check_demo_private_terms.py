#!/usr/bin/env python3
"""Fail if the built demo contains any string from a private-terms list.

The list lives OUTSIDE the repo (client names, private hosts, personal paths),
so the repo never carries the strings it guards against. One term per line;
blank lines and lines starting with # are ignored; matching is
case-insensitive over every file in the bundle. A line "term => replacement"
is also rewritten in the bundle by --rewrite (the build runs it first), for
names that sit in comments of the shared app code; the check then still
demands zero matches.

    python3 scripts/check_demo_private_terms.py out/try-demo
    CCC_PRIVATE_TERMS_FILE=/path/to/list.txt python3 scripts/check_demo_private_terms.py out/try-demo

Default list: $CCC_PRIVATE_TERMS_FILE, else ~/.config/ccc-private-terms/demo.txt.
A missing or empty list is an error (fail closed), because a check that
silently passes with no list protects nothing. Matches print the term's line
number in the list and the file, never the term itself.
"""
import os
import pathlib
import sys


def load_terms(path):
    terms = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if s and not s.startswith("#"):
            term = s.split("=>")[0].strip()
            if term:
                terms.append((n, term.lower().encode("utf-8")))
    return terms


def rewrite(bundle, path):
    """Apply "term => replacement" lines to the bundle's text files."""
    import re
    pairs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=>" in line and not line.strip().startswith("#"):
            a, b = line.split("=>", 1)
            if a.strip():
                pairs.append((re.compile(re.escape(a.strip()), re.I), b.strip()))
    changed = 0
    for f in bundle.rglob("*"):
        if not f.is_file() or f.suffix not in {".html", ".js", ".css", ".json", ".webmanifest", ".svg", ".md"}:
            continue
        text = f.read_text(encoding="utf-8")
        new = text
        for rx, rep in pairs:
            new = rx.sub(rep, new)
        if new != text:
            f.write_text(new, encoding="utf-8")
            changed += 1
    return changed


def scan(bundle, terms):
    hits = []
    for f in sorted(p for p in bundle.rglob("*") if p.is_file()):
        data = f.read_bytes().lower()
        for n, term in terms:
            if term in data:
                hits.append((n, f.relative_to(bundle)))
    return hits


def main(argv):
    args = [a for a in argv[1:] if a != "--rewrite"]
    if len(args) != 1:
        print(__doc__)
        return 2
    bundle = pathlib.Path(args[0])
    list_path = pathlib.Path(os.environ.get("CCC_PRIVATE_TERMS_FILE")
                             or "~/.config/ccc-private-terms/demo.txt").expanduser()
    if not bundle.is_dir():
        print(f"private-terms check: {bundle} is not a directory")
        return 2
    if not list_path.is_file():
        print(f"private-terms check FAILED: no list at {list_path}. "
              "Set CCC_PRIVATE_TERMS_FILE, or build with --no-private-check (never for a published bundle).")
        return 1
    if "--rewrite" in argv:
        print(f"private-terms rewrite: {rewrite(bundle, list_path)} files changed")
    terms = load_terms(list_path)
    if not terms:
        print(f"private-terms check FAILED: {list_path} has no terms")
        return 1
    hits = scan(bundle, terms)
    for n, rel in hits:
        print(f"private-terms check FAILED: term on line {n} of the list appears in {rel}")
    if hits:
        return 1
    print(f"private-terms check passed: {len(terms)} terms, no match in {bundle}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
