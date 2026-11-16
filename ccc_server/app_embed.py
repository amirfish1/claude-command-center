"""Start apps without waiting for their surrounding dashboard panels."""

import json
import re
from functools import lru_cache
from html.parser import HTMLParser

from .conversation_assets import SCRIPT_NAME, SCRIPTS


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
_ALL_SCRIPTS = re.compile(r'<script\b([^>]*)>(.*?)</script\s*>', re.S | re.I)


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
                 or attrs.get("id") in ("settingsModal", "kanbanLayout", "cccLoadingOverlay")))
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
    """Compose shared markup and controllers without the dashboard entry."""
    def keep(match):
        return "" if match[1].startswith(_PANEL_ASSETS) else match[0]

    html = _STYLE.sub(keep, _SCRIPT.sub(keep, html))
    panels = _DashboardPanels(html).ranges
    # A changed template must fall back to the full shared page, never remove
    # a partial tree. The composer and all its dialogs remain original markup.
    if len(panels) == 4:
        for start, end in sorted(panels, reverse=True):
            html = html[:start] + html[end:]
        html = html.replace('<body class="voice-call-btn-off">',
                            '<body class="voice-call-btn-off">'
                            '<div id="convList" hidden></div>'
                            '<input id="convSearch" hidden>', 1)
    # Start the bounded read and renderer download ahead of optional assets.
    html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n'
                        '<script src="/static/app-embed-boot.js"></script>\n'
                        f'<link rel="preload" as="script" href="/static/{SCRIPT_NAME}">', 1)
    # The native modules stay canonical source files for both hosts. Combine
    # their delivery so a cold app does not queue twenty separate requests.
    html = _SCRIPT.sub(lambda match: "" if match[1] in SCRIPTS else match[0], html)
    html = html.replace('<script src="/static/app.js"></script>',
                        f'<script src="/static/{SCRIPT_NAME}"></script>')
    return html.replace('<body class="voice-call-btn-off">',
                        '<body class="voice-call-btn-off conversation-popout ccc-app-embed">', 1)


def _json_element(element_id, data):
    text = json.dumps(data).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return f'<script type="application/json" id="{element_id}">{text}</script>'


def application_page(html, app_id, agent_id=None, kit_url="/static/app-kit.js"):
    """Give the shared kit its current agent without another network read."""
    if agent_id is None:
        return html
    agent_id = str(agent_id)
    if agent_id and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", agent_id):
        return html

    # Apps can append the kit dynamically after their own data loads. Keep
    # its metadata separate from the script tag so both loading styles work.
    metadata = _json_element("cccAppAgent", {"app": app_id, "session_id": agent_id})
    metadata += f'<link rel="preload" as="script" href="{kit_url}">'
    if agent_id:
        metadata += (f'<link rel="preload" as="script" href="/static/{SCRIPT_NAME}">'
                     '<link rel="preload" as="style" href="/static/app.css">')
    html = re.sub(r'(["\'])/static/app-kit\.js\1',
                  lambda match: match[1] + kit_url + match[1], html)
    head = re.search(r"<head\b[^>]*>", html, re.I)
    if head:
        return html[:head.end()] + metadata + html[head.end():]
    return metadata + html


@lru_cache(maxsize=2)
def _application_shell_template(html):
    """Transform the shared template once, rather than on each app reload."""
    def nonblocking(match):
        return match[0].replace('href=', 'data-ccc-href=', 1).replace(
            'rel="stylesheet"', 'rel="stylesheet" media="print" '
            'onload="this.media=\'all\'"', 1)

    def hold_script(match):
        attributes, code = match.groups()
        if '/static/app-shell-boot.js' in attributes or re.search(r'\btype=', attributes):
            return match[0]
        return f'<script type="application/ccc-dashboard"{attributes}>{code}</script>'

    html = _STYLE.sub(nonblocking, html)
    html = _ALL_SCRIPTS.sub(hold_script, html)
    html = html.replace('</head>',
                        '<script src="/static/app-shell-assets.js" defer></script>\n</head>', 1)
    html = html.replace('<html lang="en">',
                        '<html lang="en" class="ccc-app-starting">', 1)
    # The frame has its own styles. Hide only the unfinished dashboard,
    # not the app or standalone navigation, until dashboard controls are used.
    return html.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n'
                        '<style>html.ccc-app-starting {background:#0f1115}'
                        'html.ccc-app-starting body > :not(script):not(style)'
                        ':not(#cccMobileAppFrame):not(.ccc-side-nav):not(.ccc-rail-resizer)'
                        ':not(.ccc-report-flag):not(.ccc-account-wrap):not(.ccc-account-pop)'
                        ' {visibility:hidden}</style>', 1)


def application_shell_page(html, selected_app=None):
    """Let an app paint while its surrounding dashboard is still loading."""
    html = _application_shell_template(html)
    if selected_app:
        data = {key: selected_app[key] for key in
                ("id", "nav", "url", "label", "icon", "enabled") if key in selected_app}
        html = html.replace('<script src="/static/app-shell-boot.js">',
                            _json_element("cccAppStartup", data)
                            + '\n<script src="/static/app-shell-boot.js">', 1)
    return html
