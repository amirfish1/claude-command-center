"""Reuse index transforms and asset stamps without hiding filesystem edits."""

import os
import re
import threading

from .bounded_cache import ByteBudgetCache
from .conversation_assets import SCRIPT_NAME, SCRIPTS

_ASSET = re.compile(r'(?:src|href)="/static/([A-Za-z0-9_./-]+\.(?:js|css))"')


class IndexPageCache:
    def __init__(self):
        self._build_lock = threading.Lock()
        self.sources = ByteBudgetCache(max_bytes=4 << 20, max_entries=4)
        self.templates = ByteBudgetCache(max_bytes=8 << 20, max_entries=16)
        self.pages = ByteBudgetCache(max_bytes=8 << 20, max_entries=16)

    def load(self, source, static_dir, variant, transform, stamp):
        stat = source.stat()
        version = (str(source), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
        template_key = (version, variant)
        template = self.templates.get(template_key)
        if template is None:
            # Concurrent cold frames share one transform. The warm path never
            # waits behind unrelated page construction.
            with self._build_lock:
                template = self.templates.get(template_key)
                if template is None:
                    html = self.sources.get(version)
                    if html is None:
                        html = source.read_text(encoding="utf-8")
                        self.sources.put(version, html, len(html) * 4)
                    rendered = transform(html)
                    names = tuple(sorted(set(_ASSET.findall(rendered))))
                    template = (rendered, names)
                    self.templates.put(template_key, template, len(rendered) * 4)
        rendered, names = template
        # Stamp dependencies include assets introduced by the transform, not
        # only those in index.html. Check each time; there is no TTL window
        # during which an edited asset can retain its old immutable URL.
        versions = []
        dependencies = {dependency for name in names
                        for dependency in (SCRIPTS if name == SCRIPT_NAME else (name,))}
        for name in sorted(dependencies):
            try:
                info = os.stat(os.path.join(static_dir, name))
                versions.append((name, info.st_mtime_ns, info.st_ctime_ns, info.st_size))
            except OSError:
                versions.append((name, None, None))
        key = (template_key, str(static_dir), tuple(versions))
        page = self.pages.get(key)
        if page is None:
            page = stamp(rendered)
            self.pages.put(key, page, len(page) * 4)
        return page
