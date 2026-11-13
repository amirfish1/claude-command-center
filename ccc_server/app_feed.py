# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Findings feed for an app: <app>/feed.json, a list of cards, newest last
on disk. Research routines append with POST /api/apps/feed; static/app-feed.js
renders it. Bounded to MAX_ITEMS, written atomically. Stdlib-only."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from ccc_server import app_routines

FEED_NAME = "feed.json"
MAX_ITEMS = 200
_LIMITS = {"title": 200, "body": 2000, "url": 2000, "image": 2000, "routine": 80}
_lock = threading.Lock()


def _safe_url(value, allow_relative=False):
    value = str(value or "").strip()
    if not value:
        return ""
    if value.lower().startswith("data:image/") and allow_relative:
        return value if len(value) <= 500_000 else ""
    scheme = urlparse(value).scheme.lower()
    if scheme in ("http", "https"):
        return value
    if allow_relative and not scheme and value.startswith("/") and not value.startswith("//"):
        return value
    return ""


def load(folder):
    try:
        with open(Path(folder) / FEED_NAME, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return [i for i in data if isinstance(i, dict)] if isinstance(data, list) else []


def read(app_id, apps_dir=None):
    folder = app_routines.app_dir(app_id, apps_dir)
    if folder is None:
        return {"ok": False, "error": "unknown app"}, 404
    items = load(folder)
    items.sort(key=lambda i: i.get("at") or 0, reverse=True)
    return {"ok": True, "items": items}, 200


def append(payload, apps_dir=None, now=None):
    folder = app_routines.app_dir(payload.get("app"), apps_dir)
    if folder is None:
        return {"ok": False, "error": "unknown app"}, 404
    title = " ".join(str(payload.get("title") or "").split())[: _LIMITS["title"]]
    if not title:
        return {"ok": False, "error": "title is required"}, 400
    item = {
        "at": int(now if now is not None else time.time()),
        "title": title,
        "body": str(payload.get("body") or "").strip()[: _LIMITS["body"]],
        "url": _safe_url(payload.get("url")),
        "image": _safe_url(payload.get("image"), allow_relative=True),
        "routine": " ".join(str(payload.get("routine") or "").split())[: _LIMITS["routine"]],
    }
    with _lock:
        items = load(folder)
        items.append(item)
        items = items[-MAX_ITEMS:]
        fd, tmp = tempfile.mkstemp(prefix=".feed-", dir=str(folder))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(items, fh, ensure_ascii=False)
            os.replace(tmp, folder / FEED_NAME)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return {"ok": False, "error": "could not save feed"}, 500
    return {"ok": True, "count": len(items)}, 200
