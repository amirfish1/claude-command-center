"""Render a portable, inert HTML report of replay results."""

from collections import Counter
from html import escape
from urllib.parse import urlsplit


_OUTCOMES = (
    "fixed",
    "wrong fix",
    "no attempt or asked a question",
    "could not set up",
)
_STYLES = """
:root {
  color-scheme: light dark;
  --background: #ffffff; --text: #202124; --muted: #525960;
  --surface: #f3f4f6; --border: #767d85; --link: #1756a9;
  --fixed: #166534; --wrong: #a51d24; --no-attempt: #765000;
  --setup: #525960;
}
@media (prefers-color-scheme: dark) {
  :root {
    --background: #15171a; --text: #f1f3f5; --muted: #bcc3cb;
    --surface: #23272e; --border: #858e99; --link: #91bdff;
    --fixed: #86efac; --wrong: #ff9ba1; --no-attempt: #f6d477;
    --setup: #bcc3cb;
  }
}
* { box-sizing: border-box; }
body { margin: 0 auto; padding: 2rem 1rem; max-width: 78rem;
       background: var(--background); color: var(--text);
       font: 1rem/1.6 system-ui, sans-serif; }
h1 { line-height: 1.25; }
a { color: var(--link); text-underline-offset: .15em; }
.metadata, .note { color: var(--muted); }
.table-scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
caption { text-align: left; font-weight: bold; padding: .75rem 0; }
th, td { text-align: left; vertical-align: top; padding: .75rem;
         border-bottom: 1px solid var(--border); }
thead { background: var(--surface); }
.fixed { color: var(--fixed); }
.wrong-fix { color: var(--wrong); }
.no-attempt { color: var(--no-attempt); }
.setup { color: var(--setup); }
.outcome { font-weight: bold; }
summary { cursor: pointer; }
pre { background: var(--surface); padding: .75rem; white-space: pre-wrap;
      overflow-wrap: anywhere; font-size: .9rem; }
td:first-child { overflow-wrap: anywhere; }
"""


def _text(value):
    return escape("" if value is None else str(value), quote=True)


def _safe_url(value):
    if not isinstance(value, str) or any(ord(c) <= 32 for c in value):
        return False
    if "\\" in value:
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme == "https" and parsed.netloc.lower() == "github.com"
    except ValueError:
        return False


def _files(value):
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item) for item in value) or "None"
    return str(value) if value else "None"


def render_report(data) -> str:
    """Return standalone HTML; setup failures never enter the fixed-rate base.

    Unknown outcome labels raise ValueError rather than silently distorting
    the report's totals. All supplied values are treated as untrusted text.
    """
    cases = list(data.get("cases", []))
    counts = Counter(case.get("outcome") for case in cases)
    if any(outcome not in _OUTCOMES for outcome in counts):
        raise ValueError("Unknown replay outcome")
    evaluated = len(cases) - counts["could not set up"]
    classes = dict(zip(_OUTCOMES, ("fixed", "wrong-fix", "no-attempt", "setup")))
    rows = []
    for case in cases:
        title = _text(case.get("title", ""))
        url = case.get("url", "")
        if _safe_url(url):
            title = f'<a href="{_text(url)}">{title}</a>'
        outcome = case["outcome"]
        details = "".join(
            f"<h3>{label}</h3><pre>{_text(value)}</pre>"
            for label, value in (
                ("Reason", case.get("reason", "")),
                ("Diff", case.get("diff", "")),
                ("Changed files", _files(case.get("changed_files", []))),
                ("Overlap files", _files(case.get("overlap_files", []))),
            )
        )
        rows.append(
            f'<tr><td>{title}</td><td class="outcome {classes[outcome]}">'
            f'{_text(outcome)}</td><td>{_text(case.get("minutes", ""))}</td>'
            f'<td><details><summary>View details</summary>{details}</details></td></tr>'
        )
    headline = "; ".join(f"{counts[outcome]} {outcome}" for outcome in _OUTCOMES)
    fixed_rate = (
        f'{counts["fixed"]} / {evaluated} evaluated cases fixed.'
        if evaluated else "No evaluated cases; fixed rate unavailable."
    )
    body = "".join(rows) or '<tr><td colspan="4">No cases.</td></tr>'
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>Replay report: {_text(data.get("repo", ""))}</title>'
        f'<style>{_STYLES}</style></head><body><main>'
        '<h1>Replay report</h1>'
        f'<p class="metadata">Repository: {_text(data.get("repo", ""))} · '
        f'Days: {_text(data.get("days", ""))} · Model: {_text(data.get("model", ""))}</p>'
        f'<h2>{headline}</h2><p>{fixed_rate}</p>'
        f'<p>{_text(data.get("selection_error", ""))}</p>'
        f'<p class="note">{len(cases)} total cases; {evaluated} evaluated. '
        'Setup failures are excluded from the evaluated denominator.</p>'
        '<div class="table-scroll"><table><caption>Replay cases</caption>'
        '<thead><tr><th scope="col">Case</th><th scope="col">Outcome</th>'
        '<th scope="col">Minutes</th><th scope="col">Details</th></tr></thead>'
        f'<tbody>{body}</tbody></table></div></main></body></html>'
    )
