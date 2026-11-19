"""Deliver canonical conversation controllers together, without generated copies."""

import hashlib

from .asset_versions import STATIC_ASSET_VERSIONS


SCRIPT_NAME = "conversation-app.js"
STYLE_NAME = "conversation-base.css"
SCRIPTS = tuple("conversation-" + name + ".js" for name in (
    "state", "text", "selection", "events", "composer", "transport", "content",
    "panes", "input", "core", "defaults", "preferences", "bootstrap",
    "annotations", "chrome", "controls", "interactions", "services", "views",
    "host", "entry",
))


# Keep parser-blocking and deferred extensions in their original phases. These
# are the dashboard files themselves, never generated or forked implementations.
EXTENSIONS = (
    "claude-reauth.js", "ticket-prose.js", "block-params.js", "fx.js",
    "free-router.js", "free-runtime.js", "notify.js",
    "browser-voice.js", "choice-eligibility.js", "session-recaps.js",
    "queue-ticket-submit.js",
)
DEFERRED = (
    "limit-failover.js", "fleet-failover.js", "worker-activity.js",
    "restart-recovery.js", "question-tags.js", "credential-broker.js",
)
STYLES = (
    "simple.css", "ticket-prose.css", "fx.css", "limit-failover.css",
    "fleet-failover.css", "question-tags.css",
)
BUNDLES = {
    SCRIPT_NAME: SCRIPTS,
    STYLE_NAME: ("app.css",),
    "conversation-extensions.js": EXTENSIONS,
    "conversation-deferred.js": DEFERRED,
    "conversation-extensions.css": STYLES,
}


class ConversationScript:
    """A fixed source manifest; callers cannot supply paths or script text."""

    def __init__(self, static_dir, name=SCRIPT_NAME):
        self.static_dir = static_dir
        self.name = name
        self.sources = BUNDLES[name]

    def metadata(self):
        versions = STATIC_ASSET_VERSIONS.read(self.static_dir, self.sources)
        return self._metadata(versions)

    def _metadata(self, versions):
        if any(len(info) != 4 for info in versions):
            raise FileNotFoundError("conversation controller source missing")
        size = sum(info[3] for info in versions)
        if self.name == STYLE_NAME:
            # Change the representation version when its token-preserving
            # transform changes, so immutable browser/disk entries invalidate.
            versions = ("css-comments-v1", versions)
        version = hashlib.sha256(repr(versions).encode()).hexdigest()[:24]
        return version, (str(self.static_dir), self.name, tuple(versions)), size

    def read_bytes(self):
        # Every file retains its own native function scope. A separator protects
        # against a future module ending without a semicolon or final newline.
        separator = b"\n" if self.name.endswith(".css") else b"\n;\n"
        body = separator.join((self.static_dir / name).read_bytes() for name in self.sources)
        if self.name == STYLE_NAME:
            from .css_delivery import compact_comments
            return compact_comments(body)
        return body


def encoding_cache_keys(static_dir):
    """Keep only the current pane bundles available across dashboard restarts."""
    names = {source for sources in BUNDLES.values() for source in sources}
    versions = {entry[0]: entry for entry in STATIC_ASSET_VERSIONS.read(static_dir, names)}
    for name, sources in BUNDLES.items():
        try:
            _, key, _ = ConversationScript(static_dir, name)._metadata(
                tuple(versions[source] for source in sorted(set(sources))),
            )
        except OSError:
            continue
        yield key
        yield (key, "br")
