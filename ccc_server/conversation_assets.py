"""Deliver canonical conversation controllers together, without generated copies."""

import hashlib

from .asset_versions import STATIC_ASSET_VERSIONS


SCRIPT_NAME = "conversation-app.js"
SCRIPTS = tuple("conversation-" + name + ".js" for name in (
    "state", "text", "selection", "events", "composer", "transport", "content",
    "panes", "input", "core", "defaults", "preferences", "bootstrap",
    "annotations", "chrome", "controls", "interactions", "services", "views",
    "host", "entry",
))


class ConversationScript:
    """A fixed source manifest; callers cannot supply paths or script text."""

    def __init__(self, static_dir):
        self.static_dir = static_dir

    def metadata(self):
        versions = STATIC_ASSET_VERSIONS.read(self.static_dir, SCRIPTS)
        if any(len(info) != 4 for info in versions):
            raise FileNotFoundError("conversation controller source missing")
        size = sum(info[3] for info in versions)
        version = hashlib.sha256(repr(versions).encode()).hexdigest()[:24]
        return version, (str(self.static_dir), SCRIPT_NAME, tuple(versions)), size

    def read_bytes(self):
        # Every file retains its own native function scope. A separator protects
        # against a future module ending without a semicolon or final newline.
        return b"\n;\n".join((self.static_dir / name).read_bytes() for name in SCRIPTS)
