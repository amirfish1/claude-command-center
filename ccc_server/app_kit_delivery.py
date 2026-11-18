"""Carry the fixed app kit through existing static or dynamic script loaders."""

import base64
from functools import lru_cache
from html.parser import HTMLParser

from .asset_versions import STATIC_ASSET_VERSIONS
from .bounded_cache import ByteBudgetCache

MAX_BYTES = 64 * 1024
_POLICIES = ByteBudgetCache(max_bytes=4 << 20, max_entries=16)


class _ScriptPolicy(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.external = False
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if (tag == "meta" and (attrs.get("http-equiv") or "").strip().lower()
                == "content-security-policy") or "integrity" in attrs:
            self.external = True


@lru_cache(maxsize=4)
def _source_url(static_dir, version):
    if len(version) != 4 or version[3] > MAX_BYTES:
        return None
    with (static_dir / "app-kit.js").open("rb") as stream:
        source = stream.read(MAX_BYTES + 1)
    if len(source) > MAX_BYTES:
        return None
    return "data:application/javascript;base64," + base64.b64encode(source).decode("ascii")


def script_url(html, static_dir, fallback):
    """No app-controlled path or source is embedded; restrictive apps keep HTTP."""
    try:
        external = _POLICIES.get(html)
        if external is None:
            external = _ScriptPolicy(html).external
            _POLICIES.put(html, external, 4 * len(html) + 64)
        if external:
            return fallback
        version, = STATIC_ASSET_VERSIONS.read(static_dir, ("app-kit.js",))
        return _source_url(static_dir, version) or fallback
    except (OSError, ValueError):
        return fallback
