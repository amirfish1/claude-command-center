"""Preload canonical app assets without competing with document headers."""

import re

from . import app_routines


# These bundles are all consumed by the canonical conversation page. Hint the
# extension phases too: a preloaded renderer still waits for their downloads
# before its normal initialization can finish. Hints never execute scripts.
_ASSETS = (
    ("conversation-app.js", "script"), ("app.css", "style"),
    ("conversation-extensions.js", "script"),
    ("conversation-deferred.js", "script"),
    ("conversation-extensions.css", "style"),
)


class AppAssetHintsMixin:
    def handle_one_request(self):
        self._ccc_final_asset_hints = ()
        return super().handle_one_request()

    def end_headers(self):
        links = self._ccc_final_asset_hints
        self._ccc_final_asset_hints = ()
        for link in links:
            self.send_header("Link", link)
        return super().end_headers()


def send(handler, app_id, apps_dir, asset_url):
    """Preload assets without delaying the final response's header delivery.

    Navigation metadata limits speculative loading to browser documents.
    HTTPS proxies can relay the hints over HTTP/2; direct HTTP browsers keep
    their existing HTML preloads. Only installed apps with an agent need them.
    The caller has already applied its proxy and phone access checks.
    """
    if (handler.request_version != "HTTP/1.1"
            or handler.headers.get("Sec-Fetch-Mode") != "navigate"
            or handler.headers.get("Sec-Fetch-Dest") != "document"
            or handler.headers.get("X-Forwarded-Proto", "").lower() != "https"):
        return False
    folder = app_routines.app_dir(app_id, apps_dir)
    if folder is None:
        return False
    agent = app_routines.load_state(folder).get("agent")
    session = agent.get("session_id") if isinstance(agent, dict) else None
    if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", session):
        return False
    links = []
    for name, kind in _ASSETS:
        url = asset_url(name)
        if not re.fullmatch(r"/static/" + re.escape(name) + r"\?v=[A-Za-z0-9_.-]{1,128}", url):
            return False
        link = f"<{url}>; rel=preload; as={kind}"
        links.append(link)
    # Even extension downloads can queue ahead of the document on a shared
    # HTTP/2 connection. Deliver all preload links with its final headers so
    # the browser can start them before parsing HTML without delaying it.
    handler._ccc_final_asset_hints = tuple(links)
    return True
