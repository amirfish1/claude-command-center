"""Trim dashboard-only resources from the shared app conversation page."""

import re


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
    "first-task", "issue-flow", "star-ask", "oncall", "share-run", "app-rail",
)
_SCRIPT = re.compile(
    r'^\s*<script src="/static/([^"?]+)(?:\?[^"\n]*)?"[^>]*></script>', re.M)
_STYLE = re.compile(
    r'^\s*<link rel="stylesheet" href="/static/([^"?]+)(?:\?[^"\n]*)?"[^>]*>', re.M)


def conversation_page(html):
    """Preserve shared markup and controls, without booting hidden panels."""
    def keep(match):
        return "" if match[1].startswith(_PANEL_ASSETS) else match[0]

    html = _STYLE.sub(keep, _SCRIPT.sub(keep, html))
    # Start the bounded read and renderer download ahead of optional assets.
    html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n'
                        '<script src="/static/app-embed-boot.js"></script>\n'
                        '<link rel="preload" as="script" href="/static/app.js">', 1)
    return html.replace('<body class="voice-call-btn-off">',
                        '<body class="voice-call-btn-off conversation-popout ccc-app-embed">', 1)
