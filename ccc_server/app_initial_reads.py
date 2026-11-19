"""Opted-in initial app reads, using existing file and data API payloads."""

import json
import re
import urllib.parse
from pathlib import Path

from . import app_data, app_routines
from .app_embed import application_json

MAX_READS = 16
MAX_FILE_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 256 * 1024
_FILE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,79}\.json$')


def _small_file(path, root):
    # Only ordinary files inside this app are eligible. A symlink outside it
    # must not turn the HTML bootstrap into a new file-reading surface.
    try:
        resolved = path.resolve(strict=True)
        # read() has already canonicalized and checked this app's root.
        # Re-resolving it for every declared value repeats filesystem work.
        resolved.relative_to(root)
        if not resolved.is_file():
            return None
        with resolved.open('rb') as handle:
            body = handle.read(MAX_FILE_BYTES + 1)
        if len(body) > MAX_FILE_BYTES:
            return None
        text = body.decode('utf-8')
        json.loads(text)
        return text
    except (OSError, ValueError, UnicodeError, RuntimeError):
        return None


def read(app_id, apps_dir):
    folder = app_routines.app_dir(app_id, apps_dir)
    if folder is None:
        return {}
    try:
        folder = folder.resolve(strict=True)
        folder.relative_to(Path(apps_dir).resolve())
    except (OSError, ValueError, RuntimeError):
        return {}
    config = _small_file(folder / 'app.json', folder)
    if config is None:
        return {}
    config = json.loads(config)
    options = config.get('startup_reads') if isinstance(config, dict) else None
    if not isinstance(options, dict):
        return {}
    out, used = {}, 0

    def add(url, text):
        nonlocal used
        if text is None or url in out:
            return
        size = len(text.encode('utf-8'))
        if size > MAX_FILE_BYTES or used + size > MAX_TOTAL_BYTES:
            return
        out[url] = text
        used += size

    files = options.get('files')
    keys = options.get('data')
    files = files if isinstance(files, list) else []
    keys = keys if isinstance(keys, list) else []
    # The declared manifest itself is bounded, including invalid entries.
    for name in files[:MAX_READS]:
        if isinstance(name, str) and _FILE.fullmatch(name):
            add('/app-static/' + app_id + '/' + name,
                _small_file(folder / name, folder))
    for key in keys[:max(0, MAX_READS - min(MAX_READS, len(files)))]:
        if not app_data.valid_key(key):
            continue
        target = folder / 'data' / (key + '.json')
        # Missing data has the existing API's canonical null payload. Existing
        # files must pass the same bounded, local-file test before API parsing.
        if target.is_symlink() or (target.exists() and _small_file(target, folder) is None):
            continue
        value, status = app_data.read(app_id, key, apps_dir, max_bytes=MAX_FILE_BYTES)
        if status == 200:
            add('/api/apps/data?' + urllib.parse.urlencode({'app':app_id,'key':key}),
                json.dumps(value, separators=(',', ':')))
    return out


def application_page(html, app_id, apps_dir, *, prepare_bootstrap=None):
    try:
        reads = read(app_id, apps_dir)
    except (OSError, ValueError, TypeError):
        reads = {}
    if not reads:
        return html
    html = application_json(html, 'cccAppInitialReads', {'app':app_id,'reads':reads})
    marker = '<script data-ccc-inline-bootstrap src="/static/app-read-boot.js"></script>'
    if prepare_bootstrap is not None:
        marker = prepare_bootstrap(marker)
    # Place the adapter immediately after its inert data, before app code.
    start = html.index('id="cccAppInitialReads"')
    end = html.index('</script>', start) + len('</script>')
    return html[:end] + marker + html[end:]
