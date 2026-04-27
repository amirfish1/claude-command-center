#!/usr/bin/env python3
"""Roll up `changelog.d/` snippets into a fresh CHANGELOG.md release block.

Usage:
    python3 scripts/release.py 0.2.0

Reads every `<category>-<slug>.md` file under `changelog.d/`, groups by
the category prefix (added / changed / fixed / removed / security /
deprecated), and writes a new `## [X.Y.Z] - YYYY-MM-DD` section above
the most recent existing release entry in `CHANGELOG.md`. Snippet files
are git-rm'd after the rollup.

Version bumps in `pyproject.toml` and `server.py:__version__` are NOT
touched here — they're a separate concern handled by hand or another
script. This tool only manages the changelog.
"""
from __future__ import annotations

import datetime as _dt
import re
import subprocess
import sys
from pathlib import Path

CATEGORIES = ["Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"]
ROOT = Path(__file__).resolve().parent.parent
SNIPPET_DIR = ROOT / "changelog.d"
CHANGELOG = ROOT / "CHANGELOG.md"


def _category_for(filename: str) -> str | None:
    """Map a snippet filename to a Keep-a-Changelog category, or None."""
    stem = filename.split("-", 1)[0].lower()
    for cat in CATEGORIES:
        if cat.lower() == stem:
            return cat
    return None


def _read_bullet(path: Path) -> str:
    body = path.read_text().strip()
    if not body:
        return ""
    # Allow either pre-bulleted (`- foo`) or naked text. Normalize to
    # pre-bulleted so the rendered output is uniform.
    if not body.startswith("- "):
        body = "- " + body
    return body


def _gather() -> dict[str, list[str]]:
    by_cat: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    if not SNIPPET_DIR.is_dir():
        return by_cat
    for p in sorted(SNIPPET_DIR.glob("*.md")):
        if p.name.lower() == "readme.md":
            continue
        cat = _category_for(p.name)
        if not cat:
            print(
                f"  ! skip {p.name}: filename must start with one of "
                f"{', '.join(c.lower() for c in CATEGORIES)}",
                file=sys.stderr,
            )
            continue
        bullet = _read_bullet(p)
        if bullet:
            by_cat[cat].append((p, bullet))
    return by_cat


def _render_block(version: str, by_cat: dict[str, list[tuple[Path, str]]]) -> str:
    today = _dt.date.today().strftime("%Y-%m-%d")
    parts = [f"## [{version}] - {today}", ""]
    for cat in CATEGORIES:
        items = by_cat.get(cat, [])
        if not items:
            continue
        parts.append(f"### {cat}")
        for _, bullet in items:
            parts.append(bullet)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


_RELEASE_HEADER_RE = re.compile(r"^## \[(?!Unreleased)([^\]]+)\]")


def _splice_into_changelog(block: str) -> None:
    text = CHANGELOG.read_text()
    lines = text.split("\n")
    insert_at = None
    for i, line in enumerate(lines):
        if _RELEASE_HEADER_RE.match(line):
            insert_at = i
            break
    if insert_at is None:
        # No prior release — append at end.
        new_text = text.rstrip() + "\n\n" + block
    else:
        new_lines = lines[:insert_at] + block.rstrip().split("\n") + ["", *lines[insert_at:]]
        new_text = "\n".join(new_lines)
    CHANGELOG.write_text(new_text)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    version = argv[1].lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print(f"error: version must be X.Y.Z, got {argv[1]!r}", file=sys.stderr)
        return 2

    by_cat_paths = _gather()
    if not any(by_cat_paths.values()):
        print("error: no snippets found in changelog.d/", file=sys.stderr)
        return 1

    block = _render_block(version, by_cat_paths)
    _splice_into_changelog(block)

    paths_to_remove = [p for items in by_cat_paths.values() for p, _ in items]
    # `git rm` is preferred so the deletion lands in the same commit as
    # the CHANGELOG edit. Fall back to plain unlink if not in a git repo
    # or git is missing — the script is still useful in that case.
    try:
        subprocess.run(
            ["git", "rm", "--quiet", *(str(p.relative_to(ROOT)) for p in paths_to_remove)],
            cwd=ROOT,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"  warn: git rm failed ({e}); deleting locally instead", file=sys.stderr)
        for p in paths_to_remove:
            p.unlink(missing_ok=True)

    print(
        f"Wrote [{version}] block ({sum(len(v) for v in by_cat_paths.values())} bullets)"
        f" and removed {len(paths_to_remove)} snippet(s)."
    )
    print("Next: bump pyproject.toml + server.py:__version__, commit, tag, gh release create.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
