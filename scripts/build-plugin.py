#!/usr/bin/env python3
"""Build the Claude Code plugin bundle in plugin/ from skills/*.md.

    python3 scripts/build-plugin.py           # write plugin/
    python3 scripts/build-plugin.py --check   # exit 1 if plugin/ is stale

skills/*.md stays the source of truth (CCC installs the same files into
~/.claude/skills/<name>/SKILL.md on startup). This copies each one to
plugin/skills/<name>/SKILL.md, drops skills that were removed, and writes
plugin/.claude-plugin/plugin.json with the version from pyproject.toml.
The repo root's .claude-plugin/marketplace.json points at plugin/.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_SRC = REPO_ROOT / "skills"
PLUGIN_DIR = REPO_ROOT / "plugin"
MANIFEST = PLUGIN_DIR / ".claude-plugin" / "plugin.json"

PLUGIN_META = {
    "name": "ccc-dashboard",
    "displayName": "Claude Command Center",
    "description": ("Skills for running a fleet of coding-agent sessions through "
                    "Claude Command Center (CCC): spawn, message and check on sibling "
                    "sessions, verify fixes, and hand off work. Needs CCC running locally."),
    "author": {"name": "Amir Fish"},
    "homepage": "https://ccc.amirfish.ai",
    "repository": "https://github.com/amirfish1/claude-command-center",
    "license": "FSL-1.1-MIT",
    "keywords": ["coding-agents", "orchestration", "sessions", "dashboard"],
}


def project_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("build-plugin: no version in pyproject.toml")
    return match.group(1)


def source_skills() -> dict[str, Path]:
    return {p.stem: p for p in sorted(SKILLS_SRC.glob("*.md")) if p.stem.lower() != "readme"}


def expected_files() -> dict[Path, str]:
    """Every generated path under plugin/ mapped to its exact content."""
    manifest = {"name": PLUGIN_META["name"], "version": project_version(),
                **{k: v for k, v in PLUGIN_META.items() if k != "name"}}
    files = {MANIFEST: json.dumps(manifest, indent=2) + "\n"}
    for name, src in source_skills().items():
        files[PLUGIN_DIR / "skills" / name / "SKILL.md"] = src.read_text(encoding="utf-8")
    return files


def generated_skill_files() -> set[Path]:
    root = PLUGIN_DIR / "skills"
    return set(root.glob("*/SKILL.md")) if root.is_dir() else set()


def stale(files: dict[Path, str]) -> list[str]:
    problems = []
    for path, content in files.items():
        if not path.is_file():
            problems.append(f"missing {path.relative_to(REPO_ROOT)}")
        elif path.read_text(encoding="utf-8") != content:
            problems.append(f"out of date {path.relative_to(REPO_ROOT)}")
    for path in sorted(generated_skill_files() - set(files)):
        problems.append(f"no longer in skills/: {path.relative_to(REPO_ROOT)}")
    return problems


def build(files: dict[Path, str]) -> None:
    for path in generated_skill_files() - set(files):
        shutil.rmtree(path.parent)
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report drift, write nothing")
    args = parser.parse_args(argv)
    files = expected_files()
    problems = stale(files)
    if args.check:
        for line in problems:
            print(f"build-plugin: {line}", file=sys.stderr)
        if problems:
            print("build-plugin: run python3 scripts/build-plugin.py", file=sys.stderr)
        return 1 if problems else 0
    build(files)
    print(f"build-plugin: {len(files) - 1} skills in {PLUGIN_DIR.relative_to(REPO_ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
