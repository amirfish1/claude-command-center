"""Deliver the canonical small app bridges with their owning HTML document."""

import re

_NAMES = frozenset(('app-shell-boot.js', 'app-embed-boot.js'))
_SCRIPT = re.compile(r'<script data-ccc-inline-bootstrap src="(/static/('
                     r'app-shell-boot\.js|app-embed-boot\.js)(?:\?[^"<>]*)?)"></script>')


def mark(html, name):
    if name not in _NAMES:
        raise ValueError('unknown app bootstrap')
    return html.replace(f'<script src="/static/{name}"></script>',
                        f'<script data-ccc-inline-bootstrap src="/static/{name}"></script>')


def inline(html, static_dir):
    def replace(match):
        try:
            source = (static_dir / match[2]).read_text(encoding='utf-8')
        except OSError:
            return match[0]
        # Keep the external canonical script if a future source cannot be
        # embedded verbatim in an HTML script element. Never rewrite its JS.
        if '</script' in source.lower() or '<!--' in source:
            return match[0]
        return f'<script data-ccc-bootstrap-src="{match[1]}">{source}</script>'
    return _SCRIPT.sub(replace, html)
