"""Overlap canonical app assets with preparation of a browser's app page."""

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
_FINAL_ASSETS = {"conversation-app.js", "app.css"}


class AppAssetHintsMixin:
    def handle_one_request(self):
        self._ccc_final_asset_hints = ()
        return super().handle_one_request()

    def end_headers(self):
        links = getattr(self, "_ccc_final_asset_hints", ())
        self._ccc_final_asset_hints = ()
        for link in links:
            self.send_header("Link", link)
        return super().end_headers()


def send(handler, app_id, apps_dir, asset_url):
    """Preload assets without delaying the final response's header delivery.

    Navigation metadata excludes clients that may treat 103 as a final reply.
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
    early, final = [], []
    for name, kind in _ASSETS:
        url = asset_url(name)
        if not re.fullmatch(r"/static/" + re.escape(name) + r"\?v=[A-Za-z0-9_.-]{1,128}", url):
            return False
        link = f"<{url}>; rel=preload; as={kind}"
        (final if name in _FINAL_ASSETS else early).append(link)
    # Large preloads can fill the shared HTTP/2 connection ahead of the
    # document's final headers. Start them with those headers instead; the
    # smaller extension phases still overlap document preparation.
    handler._ccc_final_asset_hints = tuple(final)
    # Bypass final-response hooks: this has no body, status accounting, framing
    # or response timing of its own. The eventual HTML keeps all those hooks.
    links = "".join(f"Link: {link}\r\n" for link in early)
    handler.wfile.write(("HTTP/1.1 103 Early Hints\r\n" + links + "\r\n").encode("ascii"))
    handler.wfile.flush()
    return True
