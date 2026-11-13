# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Preview images for user apps.

Renders <app>/preview.png (served by /app-static/<id>/preview.png) with
scripts/app-preview.js so the Apps home can show pictures instead of live
iframes. An app is re-rendered only when its files changed since the last
render (newest file mtime > preview.png mtime) and at most every 10 minutes.
The loop checks mtimes only; node runs only when something is due.

Stdlib-only; no side effects at import.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from ccc_server.paths import COMMAND_CENTER_STATE_DIR

APPS_DIR = COMMAND_CENTER_STATE_DIR / "apps"
PREVIEW_NAME = "preview.png"
MIN_INTERVAL_SECONDS = 600
TICK_SECONDS = 60
RENDER_TIMEOUT_SECONDS = 120
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "app-preview.js"
_SKIP_NAMES = {PREVIEW_NAME, "ccc-state.json"}

_lock = threading.Lock()
_last_attempt = {}  # app_id -> monotonic time of the last render attempt


def _valid_id(value):
    from ccc_server.app_routines import valid_id
    return valid_id(value)


def newest_mtime(folder):
    """Newest mtime of the app's own files (preview and server state excluded)."""
    newest = 0.0
    try:
        for p in Path(folder).iterdir():
            if p.name in _SKIP_NAMES or p.name.startswith(".") or ".tmp" in p.name:
                continue
            try:
                if p.is_file():
                    newest = max(newest, p.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        pass
    return newest


def is_stale(folder):
    folder = Path(folder)
    try:
        have = (folder / PREVIEW_NAME).stat().st_mtime
    except OSError:
        return True
    return newest_mtime(folder) > have


def due_apps(apps_dir=None, now=None, last_attempt=None):
    """App ids needing a render now: stale and not tried in the last 10 min."""
    apps_dir = Path(apps_dir or APPS_DIR)
    now = time.monotonic() if now is None else now
    last = _last_attempt if last_attempt is None else last_attempt
    out = []
    try:
        entries = sorted(apps_dir.iterdir())
    except OSError:
        return out
    for d in entries:
        if not (d / "app.json").is_file() or not _valid_id(d.name):
            continue
        if now - last.get(d.name, -1e9) < MIN_INTERVAL_SECONDS:
            continue
        if is_stale(d):
            out.append(d.name)
    return out


def render(app_ids, port, apps_dir=None):
    """Run the puppeteer script for the given apps. Returns (ok, message)."""
    apps_dir = Path(apps_dir or APPS_DIR)
    ids = [a for a in app_ids if _valid_id(a) and (apps_dir / a / "app.json").is_file()]
    if not ids:
        return False, "no such app"
    with _lock:
        for a in ids:
            _last_attempt[a] = time.monotonic()
        try:
            r = subprocess.run(
                ["node", str(SCRIPT), f"http://127.0.0.1:{port}", str(apps_dir), *ids],
                capture_output=True, text=True, timeout=RENDER_TIMEOUT_SECONDS * len(ids),
                cwd=str(SCRIPT.parent.parent))
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)[:200]
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "render failed").strip()[-300:]
    return True, "rendered"


def request(payload, port, apps_dir=None):
    """POST /api/apps/preview {app}: render now. Returns (result, status)."""
    app_id = payload.get("app") if isinstance(payload, dict) else None
    apps_dir = Path(apps_dir or APPS_DIR)
    if not _valid_id(app_id) or not (apps_dir / app_id / "app.json").is_file():
        return {"ok": False, "error": "unknown app"}, 404
    ok, msg = render([app_id], port, apps_dir)
    if not ok:
        return {"ok": False, "error": msg}, 500
    return {"ok": True, "app": app_id, "preview": f"/app-static/{app_id}/{PREVIEW_NAME}"}, 200


def tick(port, apps_dir=None):
    ids = due_apps(apps_dir)
    if ids:
        render(ids, port, apps_dir)
    return ids


def loop(port, stop=None):
    time.sleep(45)  # let the server finish warming up
    while not (stop and stop.is_set()):
        try:
            tick(port)
        except Exception:
            pass
        time.sleep(TICK_SECONDS)
