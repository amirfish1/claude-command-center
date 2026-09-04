#!/usr/bin/env python3
"""Generate value-first release notes from a git commit range.

Where `scripts/release.py` rolls `changelog.d/` snippets into
`CHANGELOG.md` (a technical, per-change ledger), this script answers a
different question: "what can a user do now that they couldn't before,
and why should they care?" It reads real commits (not changelog.d
snippets, which don't exist for every commit), groups them into
capabilities, and rewrites each group as a value statement. Pure
plumbing commits (chore/test/docs/ci, or internal-scope fixes) collapse
into a single "Under the hood" section instead of cluttering the
headline notes.

Usage:
    python3 scripts/release_notes.py                      # last tag..HEAD
    python3 scripts/release_notes.py --range v5.30.0..HEAD
    python3 scripts/release_notes.py --since "7 days ago"
    python3 scripts/release_notes.py --out-dir docs/release-notes --stem 2026-09-03
    python3 scripts/release_notes.py --llm                # polish prose via `claude -p`

See docs/release-notes/STYLE.md for the value-first rules this script
encodes.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import html
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

RECORD_SEP = "\x02"
FIELD_SEP = "\x1f"

CONVENTIONAL_RE = re.compile(
    r"^(?P<type>[a-zA-Z]+)(\((?P<scope>[^)]+)\))?(?P<bang>!)?:\s*(?P<desc>.+)$"
)

# Commit *types* that are never user-facing capabilities on their own.
INTERNAL_TYPES = {"chore", "test", "docs", "style", "refactor", "build", "ci"}

# fix(<scope>) where the scope itself marks the change as internal
# plumbing rather than something a user would notice or care about.
INTERNAL_SCOPES = {
    "ci", "build", "release", "sprint", "restart", "hunch", "lint",
    "internal", "perf-budget", "tests", "deploy",
}

SCOPE_TITLE_OVERRIDES = {
    "ui": "Interface", "api": "API", "cli": "CLI", "acp": "ACP",
    "ci": "CI", "byok": "BYOK", "kap": "Kap",
}

BUCKET_ORDER = ["new", "improved", "fixed"]
BUCKET_LABELS = {"new": "New", "improved": "Improved", "fixed": "Fixed"}

WHY_KEYWORDS = [
    ("queue", "keeps queues moving without you babysitting them"),
    ("restart", "cuts down time spent chasing a stuck service"),
    ("spawn", "makes it clearer where a session came from"),
    ("token", "gives visibility into usage before you hit a limit"),
    ("quota", "gives visibility into usage before you hit a limit"),
    ("archive", "gets you to old conversations faster"),
    ("model-picker", "picks the right model with less clicking"),
    ("model picker", "picks the right model with less clicking"),
    ("ask", "answers questions from your own history instead of you digging for them"),
    ("mazkir", "answers questions from your own history instead of you digging for them"),
    ("alert", "surfaces problems before they sit unnoticed"),
    ("latency", "makes the app feel faster where it used to lag"),
    ("perf", "makes the app feel faster where it used to lag"),
]
WHY_DEFAULT = {
    "new": "adds a capability you didn't have before",
    "improved": "makes an existing flow faster or more reliable",
    "fixed": "removes a rough edge you may have hit without knowing why",
}


@dataclass
class Commit:
    sha: str
    subject: str
    files: list[str] = field(default_factory=list)

    @property
    def short_sha(self) -> str:
        return self.sha[:7]


@dataclass
class ParsedCommit:
    commit: Commit
    type: str | None
    scope: str | None
    desc: str
    bucket: str


def parse_conventional(subject: str) -> tuple[str | None, str | None, str]:
    """Split a conventional-commit subject into (type, scope, description).

    Falls back to (None, None, subject) for non-conforming subjects
    (merge commits, freeform messages) instead of raising.
    """
    m = CONVENTIONAL_RE.match(subject.strip())
    if not m:
        return None, None, subject.strip()
    scope = (m.group("scope") or "").strip().lower() or None
    return m.group("type").lower(), scope, m.group("desc").strip()


def classify(commit_type: str | None, scope: str | None) -> str:
    """Bucket a commit into new / improved / fixed / under_the_hood."""
    if commit_type is None:
        return "under_the_hood"
    if commit_type in INTERNAL_TYPES:
        return "under_the_hood"
    if commit_type == "feat":
        return "new"
    if commit_type in ("perf", "changed"):
        return "improved"
    if commit_type == "fix":
        if scope in INTERNAL_SCOPES:
            return "under_the_hood"
        return "fixed"
    return "under_the_hood"


def _parse_git_log_output(raw: str) -> list[Commit]:
    """Parse `git log --pretty=format:'\\x02%H\\x1f%s' --name-only` output.

    Pure function (no subprocess) so it's testable against a fixture
    string without needing a real git repo.
    """
    commits: list[Commit] = []
    for chunk in raw.split(RECORD_SEP):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        lines = chunk.split("\n")
        header = lines[0]
        if FIELD_SEP not in header:
            continue
        sha, subject = header.split(FIELD_SEP, 1)
        files = [line for line in lines[1:] if line.strip()]
        commits.append(Commit(sha=sha.strip(), subject=subject.strip(), files=files))
    return commits


def get_commits(range_spec: str | None = None, since: str | None = None,
                 cwd: Path = ROOT) -> list[Commit]:
    args = [
        "git", "log", "--no-merges",
        f"--pretty=format:{RECORD_SEP}%H{FIELD_SEP}%s", "--name-only",
    ]
    if since:
        args.append(f"--since={since}")
    if range_spec:
        args.append(range_spec)
    raw = subprocess.check_output(args, cwd=cwd, text=True)
    return _parse_git_log_output(raw)


def last_tag(cwd: Path = ROOT) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0"], cwd=cwd, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        return None


def humanize_scope(scope: str | None) -> str:
    if not scope:
        return "General"
    words = re.split(r"[-_]", scope)
    out = []
    for w in words:
        out.append(SCOPE_TITLE_OVERRIDES.get(w.lower(), w.capitalize()))
    return " ".join(out)


def why_for(descs: list[str], bucket: str) -> str:
    joined = " ".join(descs).lower()
    for kw, msg in WHY_KEYWORDS:
        if kw in joined:
            return msg
    return WHY_DEFAULT.get(bucket, WHY_DEFAULT["improved"])


@dataclass
class Capability:
    bucket: str
    scope: str | None
    commits: list[ParsedCommit]

    @property
    def title(self) -> str:
        return humanize_scope(self.scope)

    @property
    def items(self) -> list[tuple[str, str]]:
        """(what, why) pairs, one per commit — each keeps its own reason
        instead of averaging unrelated commits into one run-on sentence."""
        return [(c.desc.rstrip("."), why_for([c.desc], self.bucket)) for c in self.commits]

    @property
    def how_files(self) -> list[str]:
        files: list[str] = []
        seen = set()
        for c in self.commits:
            for f in c.commit.files:
                if f not in seen:
                    seen.add(f)
                    files.append(f)
        return files


def group_commits(commits: list[Commit]) -> tuple[dict[str, list[Capability]], list[Commit]]:
    """Classify and group commits into capabilities per bucket.

    Returns (groups_by_bucket, under_the_hood_commits). Grouping key is
    (bucket, scope); insertion order is preserved so the newest-touched
    capability leads its bucket.
    """
    order: dict[tuple[str, str | None], list[ParsedCommit]] = {}
    under_the_hood: list[Commit] = []

    for commit in commits:
        ctype, scope, desc = parse_conventional(commit.subject)
        bucket = classify(ctype, scope)
        if bucket == "under_the_hood":
            under_the_hood.append(commit)
            continue
        key = (bucket, scope)
        order.setdefault(key, []).append(
            ParsedCommit(commit=commit, type=ctype, scope=scope, desc=desc, bucket=bucket)
        )

    groups_by_bucket: dict[str, list[Capability]] = {b: [] for b in BUCKET_ORDER}
    for (bucket, scope), parsed in order.items():
        groups_by_bucket[bucket].append(Capability(bucket=bucket, scope=scope, commits=parsed))
    return groups_by_bucket, under_the_hood


def render_markdown(groups_by_bucket: dict[str, list[Capability]],
                     under_the_hood: list[Commit], range_label: str) -> str:
    today = _dt.date.today().isoformat()
    lines = [f"# CCC Release Notes — {today}", "", f"_Range: `{range_label}`_", ""]

    any_capability = False
    for bucket in BUCKET_ORDER:
        caps = groups_by_bucket.get(bucket) or []
        if not caps:
            continue
        any_capability = True
        lines.append(f"## {BUCKET_LABELS[bucket]}")
        lines.append("")
        for cap in caps:
            lines.append(f"### {cap.title}")
            for what, why in cap.items:
                lines.append(f"- **What:** {what}. **Why it matters:** {why}.")
            how = ", ".join(f"`{f}`" for f in cap.how_files[:4])
            if len(cap.how_files) > 4:
                how += ", …"
            if how:
                lines.append(f"\n**How:** {how}")
            lines.append("")

    if not any_capability:
        lines.append("No user-facing capability changes in this range.")
        lines.append("")

    if under_the_hood:
        lines.append(f"<details>")
        lines.append(f"<summary>Under the hood ({len(under_the_hood)} commits)</summary>")
        lines.append("")
        for c in under_the_hood:
            lines.append(f"- `{c.short_sha}` {c.subject}")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>CCC Release Notes — {date}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 760px; margin: 40px auto; padding: 0 20px; color: #1c1e21;
         line-height: 1.55; }}
  h1 {{ font-size: 28px; margin-bottom: 4px; }}
  .range {{ color: #6b7280; font-size: 13px; margin-bottom: 32px; }}
  h2 {{ font-size: 15px; text-transform: uppercase; letter-spacing: 0.06em;
        color: #4b5563; border-bottom: 2px solid #e5e7eb; padding-bottom: 6px;
        margin-top: 36px; }}
  .cap {{ margin: 18px 0; padding: 14px 16px; border-radius: 10px; background: #f9fafb;
          border: 1px solid #eee; }}
  .cap h3 {{ margin: 0 0 8px 0; font-size: 17px; }}
  .cap p {{ margin: 4px 0; font-size: 14.5px; }}
  .cap .how {{ color: #6b7280; font-size: 13px; }}
  .cap .how code {{ background: #eef0f2; padding: 1px 5px; border-radius: 4px; }}
  details {{ margin-top: 28px; color: #6b7280; }}
  summary {{ cursor: pointer; font-size: 13px; }}
  details ul {{ font-size: 12.5px; font-family: ui-monospace, monospace; }}
  .empty {{ color: #6b7280; font-style: italic; }}
</style>
</head>
<body>
<h1>CCC Release Notes</h1>
<div class="range">{date} &middot; range <code>{range_label}</code></div>
{body}
</body>
</html>
"""


def render_html(groups_by_bucket: dict[str, list[Capability]],
                 under_the_hood: list[Commit], range_label: str) -> str:
    today = _dt.date.today().isoformat()
    body_parts: list[str] = []
    any_capability = False

    for bucket in BUCKET_ORDER:
        caps = groups_by_bucket.get(bucket) or []
        if not caps:
            continue
        any_capability = True
        body_parts.append(f"<h2>{html.escape(BUCKET_LABELS[bucket])}</h2>")
        for cap in caps:
            how = ", ".join(f"<code>{html.escape(f)}</code>" for f in cap.how_files[:4])
            if len(cap.how_files) > 4:
                how += ", &hellip;"
            how_html = f'<p class="how"><strong>How:</strong> {how}</p>' if how else ""
            item_html = "".join(
                f"<li><strong>{html.escape(what)}.</strong> {html.escape(why)}.</li>"
                for what, why in cap.items
            )
            body_parts.append(
                '<div class="cap">'
                f"<h3>{html.escape(cap.title)}</h3>"
                f"<ul>{item_html}</ul>"
                f"{how_html}"
                "</div>"
            )

    if not any_capability:
        body_parts.append('<p class="empty">No user-facing capability changes in this range.</p>')

    if under_the_hood:
        body_parts.append("<details><summary>Under the hood "
                           f"({len(under_the_hood)} commits)</summary><ul>")
        for c in under_the_hood:
            body_parts.append(f"<li>{html.escape(c.short_sha)} {html.escape(c.subject)}</li>")
        body_parts.append("</ul></details>")

    return _HTML_TEMPLATE.format(date=today, range_label=html.escape(range_label),
                                  body="\n".join(body_parts))


def polish_with_llm(markdown: str, timeout: int = 120) -> str:
    """Optional prose pass through the local `claude -p` CLI.

    Read-only by construction (--disallowedTools blocks Bash/Write/Edit/etc,
    per the repo's own recorded invariant that --allowedTools alone does not
    restrict the toolset). Falls back to the deterministic markdown
    unchanged if the CLI is missing or errors — this pass is a polish step,
    never a requirement.
    """
    prompt = (
        "Polish the prose in this release-notes markdown. Keep every "
        "heading, bullet structure, and the <details> block byte-for-byte "
        "unchanged. Only tighten the 'What you can do now' / 'Why it "
        "matters' sentences so they read like a human product writer, not "
        "a commit log. Do not invent new claims. Return only the markdown, "
        "no commentary.\n\n" + markdown
    )
    try:
        result = subprocess.run(
            [
                "claude", "-p", prompt,
                "--allowedTools", "",
                "--disallowedTools", "Bash,Write,Edit,WebFetch,WebSearch",
                "--permission-mode", "dontAsk",
            ],
            capture_output=True, text=True, timeout=timeout, cwd=ROOT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return markdown
    out = result.stdout.strip()
    if result.returncode != 0 or not out:
        return markdown
    return out + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--range", dest="range_spec", default=None,
                         help="git revision range, e.g. v5.30.0..HEAD (default: last tag..HEAD)")
    parser.add_argument("--since", default=None,
                         help='git --since spec, e.g. "7 days ago" (overrides --range)')
    parser.add_argument("--out-dir", default=str(ROOT / "docs" / "release-notes"))
    parser.add_argument("--stem", default=None,
                         help="output filename stem (default: today's date)")
    parser.add_argument("--llm", action="store_true",
                         help="polish prose via a local `claude -p` pass (optional, degrades gracefully)")
    args = parser.parse_args(argv)

    if args.since:
        range_spec = None
        range_label = f"--since {args.since}"
        commits = get_commits(since=args.since)
    else:
        range_spec = args.range_spec
        if not range_spec:
            tag = last_tag()
            range_spec = f"{tag}..HEAD" if tag else "HEAD"
        range_label = range_spec
        commits = get_commits(range_spec=range_spec)

    groups_by_bucket, under_the_hood = group_commits(commits)
    md = render_markdown(groups_by_bucket, under_the_hood, range_label)
    if args.llm:
        md = polish_with_llm(md)
    htm = render_html(groups_by_bucket, under_the_hood, range_label)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.stem or _dt.date.today().isoformat()
    md_path = out_dir / f"{stem}.md"
    html_path = out_dir / f"{stem}.html"
    md_path.write_text(md, encoding="utf-8")
    html_path.write_text(htm, encoding="utf-8")

    print(f"Parsed {len(commits)} commits over {range_label}")
    print(f"Wrote {md_path}")
    print(f"Wrote {html_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
