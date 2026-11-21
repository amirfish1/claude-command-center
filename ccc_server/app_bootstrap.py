"""Deliver small canonical startup assets with their owning app document."""

import re

_EMBED_SCRIPTS = (
    'app-embed-boot.js', 'restart-transport.js', 'popups.js',
    'session-origin.js', 'codex-live-source.js',
)
_EMBED_STYLES = (
    'restart-transport.css', 'session-origin.css', 'credential-broker.css',
    'choice-eligibility.css', 'session-recaps.css',
)
_NAMES = frozenset(('app-shell-boot.js', 'app-read-boot.js', *_EMBED_SCRIPTS))
_SCRIPT = re.compile(r'<script data-ccc-inline-bootstrap src="(/static/('
                     + '|'.join(re.escape(name) for name in sorted(_NAMES))
                     + r')(?:\?[^"<>]*)?)"></script>')
_STYLE = re.compile(r'<link data-ccc-inline-bootstrap rel="stylesheet" href="(/static/('
                    + '|'.join(re.escape(name) for name in _EMBED_STYLES)
                    + r')(?:\?[^"<>]*)?)">')
_STYLE_EXTERNAL = re.compile(r'</style|url\s*\(|@(?:import|charset)\b|\\', re.I)


def mark(html, name):
    if name not in _NAMES:
        raise ValueError('unknown app bootstrap')
    return html.replace(f'<script src="/static/{name}"></script>',
                        f'<script data-ccc-inline-bootstrap src="/static/{name}"></script>')


def mark_embed(html):
    # Keep each asset at its original execution or cascade position. Mark
    # before stamping so the index cache still tracks each source dependency.
    for name in _EMBED_SCRIPTS:
        html = mark(html, name)
    for name in _EMBED_STYLES:
        html = html.replace(f'<link rel="stylesheet" href="/static/{name}">',
                            f'<link data-ccc-inline-bootstrap rel="stylesheet" href="/static/{name}">')
    return html


def inline(html, static_dir):
    def read(match):
        try:
            return (static_dir / match[2]).read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            return None

    def replace_script(match):
        source = read(match)
        if source is None:
            return match[0]
        # Keep the external canonical script if a future source cannot be
        # embedded verbatim in an HTML script element. Never rewrite its JS.
        if '</script' in source.lower() or '<!--' in source:
            return match[0]
        return f'<script data-ccc-bootstrap-src="{match[1]}">{source}</script>'

    def replace_style(match):
        source = read(match)
        # Keep external styles with URL resolution or stylesheet-only rules.
        if source is None or _STYLE_EXTERNAL.search(source):
            return match[0]
        return f'<style data-ccc-bootstrap-src="{match[1]}">{source}</style>'

    return _SCRIPT.sub(replace_script, _STYLE.sub(replace_style, html))
