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

BUCKET_ORDER = ["new", "improved", "fixed", "removed"]
BUCKET_LABELS = {
    "new": "New",
    "improved": "Improved",
    "fixed": "Fixed",
    "removed": "Removed",
}
MAX_SECTION_BULLETS = 12

WHY_SCOPES = {
    "ask": "answers questions from your own history instead of you digging for them",
    "archive": "gets you to old conversations faster",
    "byok": "lets each engine run with the provider keys you chose",
    "census": "makes running sessions easier to identify and triage",
    "cli": "puts the same dashboard controls in scriptable terminal commands",
    "codex": "makes Codex sessions easier to steer and read from CCC",
    "decision-inbox": "turns outside signals into trackable dashboard work",
    "grok": "makes Grok sessions show up and behave like the rest of CCC",
    "inject": "gets steered input to the intended session more reliably",
    "kimi": "makes Kimi sessions more reliable inside CCC",
    "layout": "keeps narrow screens usable without sideways drift or overlap",
    "mazkir": "answers questions from your own history instead of you digging for them",
    "messaging": "gets agent-to-agent messages delivered with less manual recovery",
    "model-picker": "picks the right model with less clicking",
    "models": "makes model choice easier to compare before you start",
    "orchestration": "makes multi-agent work easier to follow from the lane map",
    "orch": "makes multi-agent work easier to follow from the lane map",
    "perf": "makes the app feel faster where it used to lag",
    "processes": "makes runaway or stale processes easier to spot and stop",
    "queue": "keeps queues moving without you babysitting them",
    "queue-panel": "keeps queues moving without you babysitting them",
    "search": "finds relevant recent work without a manual archive dig",
    "sessions": "keeps session lists accurate after lifecycle changes",
    "sidebar": "makes related sessions easier to scan and open",
    "spawn": "makes it clearer where a session came from",
    "tickets": "makes ticket details readable without opening another tool",
    "uds": "gets agent-to-agent messages delivered with less manual recovery",
    "worker": "keeps background workers aligned with the code they run",
}

WHY_KEYWORDS = [
    ("ack", "keeps queues moving without you babysitting them"),
    ("alert", "surfaces problems before they sit unnoticed"),
    ("approval", "lets you act on blocked agent work from the dashboard"),
    ("archive", "gets you to old conversations faster"),
    ("ask", "answers questions from your own history instead of you digging for them"),
    ("attach", "makes it easier to send files and screenshots into a session"),
    ("byok", "lets each engine run with the provider keys you chose"),
    ("context-usage", "gives visibility into usage before you hit a limit"),
    ("context usage", "gives visibility into usage before you hit a limit"),
    ("coo board", "keeps the dashboard focused on active command-center workflows"),
    ("conversation row", "makes session lists easier to scan"),
    ("engine", "makes model choice easier to compare before you start"),
    ("github rate", "surfaces sync failures in terms you can act on"),
    ("kap", "makes Kimi sessions more reliable inside CCC"),
    ("lane", "makes multi-agent work easier to follow from the lane map"),
    ("latency", "makes the app feel faster where it used to lag"),
    ("mazkir", "answers questions from your own history instead of you digging for them"),
    ("model picker", "picks the right model with less clicking"),
    ("model-picker", "picks the right model with less clicking"),
    ("peer", "gets agent-to-agent messages delivered with less manual recovery"),
    ("perf", "makes the app feel faster where it used to lag"),
    ("process", "makes runaway or stale processes easier to spot and stop"),
    ("q2", "keeps queues moving without you babysitting them"),
    ("queue", "keeps queues moving without you babysitting them"),
    ("quota", "gives visibility into usage before you hit a limit"),
    ("spawn", "makes it clearer where a session came from"),
    ("steer", "gets steered input to the intended session more reliably"),
    ("ticket", "makes ticket details readable without opening another tool"),
    ("token", "gives visibility into usage before you hit a limit"),
    ("transport", "makes engine routing visible before you steer"),
    ("watchtower", "keeps queues moving without you babysitting them"),
]

NOUN_STOPWORDS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "is",
    "it", "its", "no", "not", "of", "on", "or", "over", "per", "the", "to",
    "via", "with", "without",
    "accept", "accepted", "action", "actions", "active", "add", "added",
    "adds", "allow", "allows", "answer", "answers", "app", "bar", "bind",
    "bring", "button", "cap", "capture", "ccc", "centralize", "chip",
    "chips", "classify", "collapse", "cold", "connect", "controls",
    "correct", "dashboard", "dedupe", "default", "detect", "engine",
    "engines",
    "differentiate", "discover", "draw", "drop", "enable", "exclude",
    "expose", "fill", "finalize", "fix", "focus", "fold", "freeze", "give",
    "guard", "harden", "heal", "hint", "ingest", "invalidate", "keep",
    "label", "let", "make", "map", "measure", "migrate", "move", "nest",
    "normalize", "pass", "place", "preserve", "protect", "prune", "publish",
    "list", "mode", "open", "opens", "pane", "pull", "queue", "queued",
    "queues", "read", "reader", "rebind", "reconcile", "record",
    "redesign", "register", "reject", "remove", "render", "repair",
    "reserve", "resolve", "restore", "route", "row", "rows", "run", "scan",
    "screen", "screens", "serialize", "server", "session", "sessions", "set",
    "show", "sort", "stamp", "status", "stays", "stop", "strip", "surface",
    "suppress", "system", "tab", "tabs", "tag", "thread", "tighten",
    "toolbar", "transcript", "trim", "trust", "use", "validate", "verify",
    "view", "views", "wire", "wrap",
}

REMOVAL_RE = re.compile(r"^(remove|drop|delete|retire|deprecate)\s+(?P<thing>.+)$", re.I)
RENAME_RE = re.compile(r"^(rename|renames|renamed)\s+(?P<thing>.+)$", re.I)


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


def is_removal(desc: str) -> bool:
    return bool(REMOVAL_RE.match(desc.strip()) or RENAME_RE.match(desc.strip()))


def _keyword_matches(text: str, keyword: str) -> bool:
    return bool(re.search(
        rf"(?<![a-z0-9]){re.escape(keyword.lower())}(?![a-z0-9])",
        text.lower(),
    ))


def why_for(descs: list[str], bucket: str, scope: str | None = None,
            files: list[str] | None = None) -> str | None:
    joined = " ".join([scope or "", *(descs or [])]).lower()
    if scope in WHY_SCOPES:
        return WHY_SCOPES[scope]
    for kw, msg in WHY_KEYWORDS:
        if _keyword_matches(joined, kw):
            return msg
    return None


def _noun_terms(desc: str) -> set[str]:
    normalized = re.sub(r"[^a-zA-Z0-9]+", " ", desc.lower())
    terms = {
        token for token in normalized.split()
        if len(token) > 2 and token not in NOUN_STOPWORDS and not token.isdigit()
    }
    expanded = set(terms)
    for token in terms:
        if "-" in token:
            expanded.update(part for part in token.split("-") if len(part) > 2)
    return expanded


def _clean_desc(desc: str) -> str:
    return desc.strip().rstrip(".")


def _capitalized(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    return text[0].upper() + text[1:]


def _rewrite_desc(desc: str) -> str:
    text = _clean_desc(desc)
    removal = REMOVAL_RE.match(text)
    if removal:
        thing = _clean_desc(removal.group("thing"))
        return f"{_capitalized(thing)} is gone"
    rename = RENAME_RE.match(text)
    if rename:
        return f"{_capitalized(_clean_desc(rename.group('thing')))} is renamed"

    replacements = [
        (r"^add\s+(.+)$", r"\1 is available"),
        (r"^surface\s+(.+)$", r"\1 is visible"),
        (r"^show\s+(.+)$", r"\1 is visible"),
        (r"^record\s+(.+)$", r"\1 is recorded"),
        (r"^wire\s+(.+)$", r"\1 is wired in"),
        (r"^route\s+(.+)$", r"\1 routes correctly"),
        (r"^keep\s+(.+)$", r"\1 stays in place"),
        (r"^stop\s+(.+)$", r"\1 no longer happens"),
        (r"^restore\s+(.+)$", r"\1 is restored"),
        (r"^accept\s+(.+)$", r"\1 is accepted"),
        (r"^enable\s+(.+)$", r"\1 is enabled"),
        (r"^make\s+(.+)$", r"\1 works correctly"),
        (r"^fix\s+(.+)$", r"\1 works correctly"),
    ]
    for pattern, repl in replacements:
        rewritten = re.sub(pattern, repl, text, flags=re.I)
        if rewritten != text:
            return _capitalized(rewritten)
    return _capitalized(text)


@dataclass
class Capability:
    bucket: str
    scope: str | None
    commits: list[ParsedCommit]
    sequence: int = 0

    @property
    def title(self) -> str:
        return humanize_scope(self.scope)

    @property
    def terms(self) -> set[str]:
        terms: set[str] = set()
        for c in self.commits:
            terms.update(_noun_terms(c.desc))
        return terms

    @property
    def what(self) -> str:
        slices = [_rewrite_desc(c.desc) for c in self.commits]
        if not slices:
            return ""
        if len(slices) == 1:
            return slices[0]
        return f"{slices[0]}; includes " + "; ".join(slices[1:])

    @property
    def why(self) -> str | None:
        return why_for(
            [c.desc for c in self.commits],
            self.bucket,
            self.scope,
            self.how_files,
        )

    @property
    def evidence_weight(self) -> tuple[int, int, int]:
        return (len(self.commits), len(self.how_files), -self.sequence)

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


def _add_to_capability(caps: list[Capability], parsed: ParsedCommit, sequence: int) -> None:
    terms = _noun_terms(parsed.desc)
    for cap in caps:
        if terms and terms & cap.terms:
            cap.commits.append(parsed)
            return
    caps.append(Capability(
        bucket=parsed.bucket,
        scope=parsed.scope,
        commits=[parsed],
        sequence=sequence,
    ))


def _ranked_caps(caps: list[Capability]) -> list[Capability]:
    return sorted(caps, key=lambda cap: cap.evidence_weight, reverse=True)


def _section_caps(caps: list[Capability]) -> tuple[list[Capability], int]:
    ranked = _ranked_caps(caps)
    return ranked[:MAX_SECTION_BULLETS], max(0, len(ranked) - MAX_SECTION_BULLETS)


def _under_the_hood_commits(commits: list[Commit]) -> tuple[list[Commit], int]:
    ranked = sorted(commits, key=lambda commit: (len(commit.files), commit.short_sha), reverse=True)
    return ranked[:MAX_SECTION_BULLETS], max(0, len(ranked) - MAX_SECTION_BULLETS)


def group_commits(commits: list[Commit]) -> tuple[dict[str, list[Capability]], list[Commit]]:
    """Classify and group commits into capabilities per bucket.

    Returns (groups_by_bucket, under_the_hood_commits). Grouping key is
    (bucket, scope); insertion order is preserved so the newest-touched
    capability leads its bucket.
    """
    order: dict[tuple[str, str | None], list[Capability]] = {}
    under_the_hood: list[Commit] = []

    for sequence, commit in enumerate(commits):
        ctype, scope, desc = parse_conventional(commit.subject)
        bucket = classify(ctype, scope)
        if bucket != "under_the_hood" and is_removal(desc):
            bucket = "removed"
        if bucket == "under_the_hood":
            under_the_hood.append(commit)
            continue
        key = (bucket, scope)
        _add_to_capability(
            order.setdefault(key, []),
            ParsedCommit(commit=commit, type=ctype, scope=scope, desc=desc, bucket=bucket),
            sequence,
        )

    groups_by_bucket: dict[str, list[Capability]] = {b: [] for b in BUCKET_ORDER}
    for (bucket, _scope), caps in order.items():
        for cap in caps:
            if cap.why is None:
                under_the_hood.extend(c.commit for c in cap.commits)
            else:
                groups_by_bucket[bucket].append(cap)
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
        visible_caps, hidden_count = _section_caps(caps)
        active_title = None
        for cap in visible_caps:
            if cap.title != active_title:
                active_title = cap.title
                lines.append(f"### {cap.title}")
            lines.append(f"- **What:** {cap.what}. **Why it matters:** {cap.why}.")
            how = ", ".join(f"`{f}`" for f in cap.how_files[:4])
            if len(cap.how_files) > 4:
                how += ", …"
            if how:
                lines.append(f"\n**How:** {how}")
            lines.append("")
        if hidden_count:
            lines.append(f"- and {hidden_count} more {BUCKET_LABELS[bucket]} capabilities.")
            lines.append("")

    if not any_capability:
        lines.append("No user-facing capability changes in this range.")
        lines.append("")

    if under_the_hood:
        lines.append(f"<details>")
        lines.append(f"<summary>Under the hood ({len(under_the_hood)} commits)</summary>")
        lines.append("")
        visible_commits, hidden_count = _under_the_hood_commits(under_the_hood)
        for c in visible_commits:
            lines.append(f"- `{c.short_sha}` {c.subject}")
        if hidden_count:
            lines.append(f"- and {hidden_count} more under-the-hood commits.")
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
        visible_caps, hidden_count = _section_caps(caps)
        scope_parts: dict[str, list[Capability]] = {}
        for cap in visible_caps:
            scope_parts.setdefault(cap.title, []).append(cap)
        for title, scoped_caps in scope_parts.items():
            item_html = ""
            how_files: list[str] = []
            seen_files = set()
            for cap in scoped_caps:
                item_html += (
                    f"<li><strong>{html.escape(cap.what)}.</strong> "
                    f"{html.escape(cap.why or '')}.</li>"
                )
                for f in cap.how_files:
                    if f not in seen_files:
                        seen_files.add(f)
                        how_files.append(f)
            how = ", ".join(f"<code>{html.escape(f)}</code>" for f in how_files[:4])
            if len(how_files) > 4:
                how += ", &hellip;"
            how_html = f'<p class="how"><strong>How:</strong> {how}</p>' if how else ""
            body_parts.append(
                '<div class="cap">'
                f"<h3>{html.escape(title)}</h3>"
                f"<ul>{item_html}</ul>"
                f"{how_html}"
                "</div>"
            )
        if hidden_count:
            body_parts.append(
                f"<p>and {hidden_count} more "
                f"{html.escape(BUCKET_LABELS[bucket])} capabilities.</p>"
            )

    if not any_capability:
        body_parts.append('<p class="empty">No user-facing capability changes in this range.</p>')

    if under_the_hood:
        body_parts.append("<details><summary>Under the hood "
                           f"({len(under_the_hood)} commits)</summary><ul>")
        visible_commits, hidden_count = _under_the_hood_commits(under_the_hood)
        for c in visible_commits:
            body_parts.append(f"<li>{html.escape(c.short_sha)} {html.escape(c.subject)}</li>")
        if hidden_count:
            body_parts.append(f"<li>and {hidden_count} more under-the-hood commits.</li>")
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
