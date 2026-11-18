"""Overlap canonical app assets with preparation of a browser's app page."""

import re

from . import app_routines


def send(handler, app_id, apps_dir, asset_url):
    """Send one informational response; leave the final response untouched.

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
    links = []
    for name, kind in (("conversation-app.js", "script"), ("app.css", "style")):
        url = asset_url(name)
        if not re.fullmatch(r"/static/" + re.escape(name) + r"\?v=[A-Za-z0-9_.-]{1,128}", url):
            return False
        links.append(f"Link: <{url}>; rel=preload; as={kind}\r\n")
    # Bypass final-response hooks: this has no body, status accounting, framing
    # or response timing of its own. The eventual HTML keeps all those hooks.
    handler.wfile.write(("HTTP/1.1 103 Early Hints\r\n" + "".join(links) + "\r\n").encode("ascii"))
    handler.wfile.flush()
    return True
