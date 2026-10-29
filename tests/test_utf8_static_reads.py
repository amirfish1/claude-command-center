"""Static HTML must be read as UTF-8: Windows defaults text I/O to cp1252 and
static/index.html contains bytes cp1252 cannot decode (OPS-1429)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [ROOT / "server.py", *sorted((ROOT / "ccc_server").glob("*.py"))]
HTML_READ = re.compile(r"(?:_INDEX_HTML_PATH|\.html[\"'])[^\n]*\.read_text\(\s*\)")


def test_static_html_reads_name_utf8():
    offenders = [
        f"{p.name}:{n}"
        for p in SOURCES
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if HTML_READ.search(line)
    ]
    assert not offenders, f"read_text() without encoding on HTML: {offenders}"


def test_index_html_is_not_cp1252_decodable_without_encoding_arg():
    # documents why the encoding argument is required
    data = (ROOT / "static" / "index.html").read_bytes()
    try:
        data.decode("cp1252")
    except UnicodeDecodeError:
        return
    raise AssertionError("index.html now decodes as cp1252; test premise changed")
