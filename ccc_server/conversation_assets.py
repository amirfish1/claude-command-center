"""Deliver canonical conversation controllers together, without generated copies."""

import hashlib


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
        versions = []
        size = 0
        for name in SCRIPTS:
            info = (self.static_dir / name).stat()
            versions.append((name, info.st_mtime_ns, info.st_ctime_ns, info.st_size))
            size += info.st_size
        version = hashlib.sha256(repr(versions).encode()).hexdigest()[:24]
        return version, (str(self.static_dir), SCRIPT_NAME, tuple(versions)), size

    def read_bytes(self):
        # Every file retains its own native function scope. A separator protects
        # against a future module ending without a semicolon or final newline.
        return b"\n;\n".join((self.static_dir / name).read_bytes() for name in SCRIPTS)
