"""Start apps without waiting for their surrounding dashboard panels."""

import re
from functools import lru_cache
from html.parser import HTMLParser


# Keep the renderer, composer, speech, recovery and conversation extensions on
# the same source as the dashboard. These modules only serve hidden panels.
_PANEL_ASSETS = (
    "settings-", "free-settings", "free-key-wizard", "domestic-providers",
    "onboarding/", "savings", "headroom-bars", "sidebar-", "federated-sidebar",
    "phone-access", "cloud-relay", "config-consent", "queue-attention",
    "q2-worker-idle", "wt-log-bursts",
    "router-detected", "queue-policy", "voice-setup", "engine-connection",
    "service-connections", "mobile-ticket-record",
    "leftover", "runaway", "scheduled-jobs", "jobs-tab", "decision-tab",
    "first-task", "issue-flow", "star-ask", "oncall", "share-run", "app-rail", "app-shell-",
)
_SCRIPT = re.compile(
    r'^\s*<script src="/static/([^"?]+)(?:\?[^"\n]*)?"[^>]*></script>', re.M)
_STYLE = re.compile(
    r'^\s*<link rel="stylesheet" href="/static/([^"?]+)(?:\?[^"\n]*)?"[^>]*>', re.M)


class _DashboardPanels(HTMLParser):
    """Locate complete hidden dashboard trees without copying pane markup."""
    _VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())

    def __init__(self, html):
        super().__init__()
        self.offsets = [0]
        self.offsets.extend(match.end() for match in re.finditer("\n", html))
        self.stack = []
        self.ranges = []
        self.feed(html)

    def _source_offset(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attributes):
        if tag in self._VOID:
            return
        attrs = dict(attributes)
        panel = (tag == "div" and (attrs.get("class") == "sidebar"
                 or attrs.get("id") in ("settingsModal", "kanbanLayout")))
        self.stack.append((tag, self._source_offset() if panel else None))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] != tag:
                continue
            for _, start in self.stack[index:]:
                if start is not None:
                    self.ranges.append((start, self._source_offset() + len(tag) + 3))
            del self.stack[index:]
            break


@lru_cache(maxsize=2)
def conversation_page(html):
    """Preserve shared markup and controls, without booting hidden panels."""
    def keep(match):
        return "" if match[1].startswith(_PANEL_ASSETS) else match[0]

    html = _STYLE.sub(keep, _SCRIPT.sub(keep, html))
    panels = _DashboardPanels(html).ranges
    # A changed template must fall back to the full shared page, never remove
    # a partial tree. The composer and all its dialogs remain original markup.
    if len(panels) == 3:
        for start, end in sorted(panels, reverse=True):
            html = html[:start] + html[end:]
        html = html.replace('<body class="voice-call-btn-off">',
                            '<body class="voice-call-btn-off">'
                            '<div id="convList" hidden></div>'
                            '<input id="convSearch" hidden>', 1)
    # Start the bounded read and renderer download ahead of optional assets.
    html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n'
                        '<script src="/static/app-embed-boot.js"></script>\n'
                        '<link rel="preload" as="script" href="/static/app.js">', 1)
    return html.replace('<body class="voice-call-btn-off">',
                        '<body class="voice-call-btn-off conversation-popout ccc-app-embed">', 1)


def application_shell_page(html):
    """Let an app paint while its surrounding dashboard is still loading."""
    def nonblocking(match):
        ready = (";document.documentElement.classList.remove('ccc-app-starting')"
                 if match[1] == "app.css" else "")
        failed = f' onerror="document.documentElement.classList.remove(\'ccc-app-starting\')"' if ready else ""
        return match[0].replace('rel="stylesheet"',
                               'rel="stylesheet" media="print" '
                               f'onload="this.media=\'all\'{ready}"{failed}', 1)

    html = _STYLE.sub(nonblocking, html)
    html = html.replace('<html lang="en">',
                        '<html lang="en" class="ccc-app-starting">', 1)
    # The frame has its own styles. Hide only the unfinished dashboard,
    # not the app, until the main dashboard stylesheet has arrived.
    return html.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n'
                        '<style>html.ccc-app-starting {background:#0f1115}'
                        'html.ccc-app-starting body > :not(script):not(style)'
                        ':not(#cccMobileAppFrame) {visibility:hidden}</style>', 1)
