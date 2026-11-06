#!/usr/bin/env python3
"""Rebuild ccc_server/share_wordlist.txt, the redactor's word allow-list.

The share export masks every word that is not on this list, so a name it has
never seen cannot leak. The list only needs ordinary English and programming
vocabulary. It is derived from prose that is already public, the Python
standard library and the Markdown files of installed npm packages, counting
only LOWERCASE occurrences: people's names are capitalized in running text,
so they rarely qualify. Names that do qualify are removed at runtime by
ccc_server/share_redact.py (NAME_WORDS).

Usage: python3 scripts/build_share_wordlist.py [--min-files N] [ROOT ...]
"""
import argparse
import collections
import re
import sys
import sysconfig
from pathlib import Path

WORD = re.compile(r"(?<![A-Za-z0-9_.\-/@])[a-z]{2,18}(?=[ ,.;:!?)\n])")
SUFFIXES = (".py", ".js", ".ts", ".md", ".rst", ".txt", ".pod", ".h")
OUT = Path(__file__).resolve().parents[1] / "ccc_server" / "share_wordlist.txt"


def corpus(roots):
    for root in roots:
        for path in Path(root).rglob("*"):
            if path.suffix in SUFFIXES and path.is_file():
                yield path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-files", type=int, default=4)
    parser.add_argument("roots", nargs="*")
    args = parser.parse_args()
    roots = args.roots or [sysconfig.get_paths()["stdlib"], "node_modules"]  # add more on the CLI
    seen = collections.Counter()
    for path in corpus(roots):
        try:
            text = path.read_text("utf-8", "ignore")[:2_000_000]
        except OSError:
            continue
        seen.update(set(WORD.findall(text)))
    words = sorted(w for w, n in seen.items() if n >= args.min_files)
    OUT.write_text("\n".join(words) + "\n", encoding="utf-8")
    print(f"{len(words)} words -> {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
