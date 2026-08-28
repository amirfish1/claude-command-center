"""Keep em dashes out of user-visible copy in the main static surfaces.

The source files contain many explanatory comments and a small number of
protocol delimiters. Those are not UI copy. This test inspects rendered HTML
text, HTML attributes, and JavaScript string/template literals while skipping
comments and regular-expression literals.
"""

from html.parser import HTMLParser
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
STATIC_FILES = (
    "static/app.js",
    "static/throughput.html",
    "static/productivity.html",
    "static/throughput-daily.html",
    "static/index.html",
)
EM_DASH = "\N{EM DASH}"
_ALLOWED_PROTOCOL_CONTEXTS = (
    "parts.join(' — ')",
    "p.join(' — ')",
    "const stripRe = new RegExp(",
)


def _regex_can_start_after(token):
    return token is None or token in {
        "(", "[", "{", "=", ":", ",", ";", "!", "?", "&", "|",
        "+", "-", "*", "%", "^", "~", "<", ">", "=>", "return",
        "case", "throw", "delete", "void", "typeof", "instanceof", "in",
        "of", "else", "do", "yield", "await",
    }


def _js_string_literals(source, first_line=1):
    """Yield ``(line, literal, source_line)`` without treating comments as copy."""

    i = 0
    line = first_line
    previous_token = None
    length = len(source)
    source_lines = source.splitlines()

    while i < length:
        char = source[i]
        following = source[i + 1] if i + 1 < length else ""

        if char.isspace():
            if char == "\n":
                line += 1
            i += 1
            continue

        if char == "/" and following == "/":
            newline = source.find("\n", i + 2)
            if newline < 0:
                return
            i = newline
            continue

        if char == "/" and following == "*":
            end = source.find("*/", i + 2)
            if end < 0:
                return
            line += source.count("\n", i, end + 2)
            i = end + 2
            continue

        if char == "/" and _regex_can_start_after(previous_token):
            i += 1
            escaped = False
            in_class = False
            while i < length:
                char = source[i]
                if char == "\n":
                    line += 1
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == "[":
                    in_class = True
                elif char == "]" and in_class:
                    in_class = False
                elif char == "/" and not in_class:
                    i += 1
                    while i < length and source[i].isalpha():
                        i += 1
                    previous_token = "regex"
                    break
                i += 1
            continue

        if char in ("'", '"', "`"):
            quote = char
            start = i
            start_line = line
            i += 1
            escaped = False
            while i < length:
                char = source[i]
                if char == "\n":
                    line += 1
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    i += 1
                    literal = source[start:i]
                    source_line = source_lines[start_line - first_line]
                    yield start_line, literal, source_line.strip()
                    previous_token = "string"
                    break
                i += 1
            continue

        if char.isalpha() or char in ("_", "$"):
            end = i + 1
            while end < length and (source[end].isalnum() or source[end] in ("_", "$")):
                end += 1
            previous_token = source[i:end]
            i = end
            continue

        if char.isdigit():
            end = i + 1
            while end < length and (source[end].isalnum() or source[end] in ".xX_"):
                end += 1
            previous_token = "number"
            i = end
            continue

        if source.startswith("=>", i):
            previous_token = "=>"
            i += 2
            continue

        previous_token = char
        i += 1


class _VisibleHtmlCopy(HTMLParser):
    def __init__(self, filename):
        HTMLParser.__init__(self, convert_charrefs=False)
        self.filename = filename
        self.issues = []
        self._raw_tag = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._raw_tag = tag
        line, _ = self.getpos()
        for attr_name, value in attrs:
            if value and EM_DASH in value:
                self.issues.append((line, "attribute " + attr_name, value.strip()))

    def handle_endtag(self, tag):
        if tag == self._raw_tag:
            self._raw_tag = None

    def handle_data(self, data):
        line, _ = self.getpos()
        if self._raw_tag == "script":
            for string_line, literal, _source_line in _js_string_literals(data, line):
                if EM_DASH in literal:
                    self.issues.append((string_line, "JavaScript string", literal.strip()))
        elif self._raw_tag != "style" and EM_DASH in data:
            self.issues.append((line, "rendered text", re.sub(r"\s+", " ", data).strip()))


def _em_dash_issues(relative_path):
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    if relative_path.endswith(".js"):
        issues = []
        for line, literal, source_line in _js_string_literals(source):
            if EM_DASH not in literal:
                continue
            if any(context in source_line for context in _ALLOWED_PROTOCOL_CONTEXTS):
                continue
            issues.append((line, "JavaScript string", literal.strip(), source_line))
        return issues

    parser = _VisibleHtmlCopy(relative_path)
    parser.feed(source)
    return [(line, kind, text, "") for line, kind, text in parser.issues]


def test_user_visible_static_copy_has_no_em_dashes():
    issues = []
    for relative_path in STATIC_FILES:
        for line, kind, text, source_line in _em_dash_issues(relative_path):
            excerpt = re.sub(r"\s+", " ", text)
            if len(excerpt) > 180:
                excerpt = excerpt[:177] + "..."
            issues.append(
                "{}:{} [{}] {}{}".format(
                    relative_path,
                    line,
                    kind,
                    excerpt,
                    " | " + source_line if source_line else "",
                )
            )

    assert not issues, "User-visible em dashes remain:\n" + "\n".join(issues)
