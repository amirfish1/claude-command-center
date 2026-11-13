# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Per-app JSON data: small values an app page saves on the server.

An app page can keep answers (ticks, hidden rows, notes) in the app's own
folder instead of only in browser localStorage, so every device and every
agent reads the same file:

    GET  /api/apps/data?app=<id>&key=<name>   -> {ok, value, updated}
    POST /api/apps/data {app, key, value}      -> {ok, updated}

Each key is one plain JSON file, <app folder>/data/<key>.json, written
atomically. Stdlib-only; no side effects at import.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

from ccc_server.app_routines import app_dir

MAX_VALUE_BYTES = 256 * 1024
_KEY_RE = re.compile(r"^[a-z0-9_-]{1,40}$")


def valid_key(key):
    return isinstance(key, str) and bool(_KEY_RE.match(key))


def _path(app_id, key, apps_dir):
    folder = app_dir(app_id, apps_dir)
    if folder is None:
        return None, ({"ok": False, "error": "unknown app"}, 404)
    if not valid_key(key):
        return None, ({"ok": False, "error": "bad key"}, 400)
    return folder / "data" / (key + ".json"), None


def read(app_id, key, apps_dir=None):
    path, err = _path(app_id, key, apps_dir)
    if err:
        return err
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        return {"ok": True, "value": doc.get("value"), "updated": doc.get("updated")}, 200
    except FileNotFoundError:
        return {"ok": True, "value": None, "updated": None}, 200
    except (OSError, ValueError, AttributeError):
        return {"ok": False, "error": "could not read"}, 500


def write(payload, apps_dir=None):
    if not isinstance(payload, dict) or "value" not in payload:
        return {"ok": False, "error": "value required"}, 400
    path, err = _path(payload.get("app"), payload.get("key"), apps_dir)
    if err:
        return err
    updated = int(time.time())
    try:
        blob = json.dumps({"value": payload["value"], "updated": updated},
                          ensure_ascii=False, indent=1).encode("utf-8")
    except (TypeError, ValueError):
        return {"ok": False, "error": "value is not JSON"}, 400
    if len(blob) > MAX_VALUE_BYTES:
        return {"ok": False, "error": "value too large"}, 413
    try:
        path.parent.mkdir(exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        return {"ok": False, "error": "could not save"}, 500
    return {"ok": True, "updated": updated}, 200
