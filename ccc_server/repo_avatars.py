"""Local repository logos and machine-local avatar overrides (stdlib only)."""
import base64
import binascii
import hashlib
import html
import itertools
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time

MAX_IMAGE = 2 * 1024 * 1024
TYPES = {'.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg',
         '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.gif': 'image/gif',
         '.ico': 'image/x-icon'}
_DIRS = ('', 'public', 'static', 'assets', 'src/assets', 'app', 'src/app',
         'docs/brand', 'docs/images', 'docs/assets', '.github')
_NAMES = ('logo', 'icon', 'apple-touch-icon', 'favicon', 'app-icon')
_lock = threading.Lock()
_cache = {}


def _override(state_dir, repo):
    key = hashlib.sha256(str(repo).encode()).hexdigest()
    return Path(state_dir) / 'repo-avatars' / (key + '.json')


def _image(candidate, root):
    """Confine symlinks and reads; never read pipes, devices or oversized files."""
    try:
        target = candidate.resolve()
        target.relative_to(root)
        if target.suffix.lower() not in TYPES:
            return None
        info = target.stat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_IMAGE:
            return None
        with target.open('rb') as stream:
            data = stream.read(MAX_IMAGE + 1)
        if len(data) > MAX_IMAGE:
            return None
        return data, TYPES[target.suffix.lower()], str(target.relative_to(root))
    except (OSError, ValueError, RuntimeError):
        return None


def _discover(root):
    # Fixed, shallow locations avoid walking dependencies and build output.
    for name in _NAMES:
        for directory in _DIRS:
            for ext in TYPES:
                hit = _image(root / directory / (name + ext), root)
                if hit:
                    return hit
    # Branded filenames (logo-light.svg etc.) are common too.
    for directory in _DIRS:
        try:
            entries = sorted(itertools.islice((root / directory).iterdir(), 256), key=lambda p: p.name)
        except (OSError, RuntimeError):
            continue
        for entry in entries:
            if re.match(r'^(?:logo|favicon|app-icon)[-_.]', entry.name, re.I):
                hit = _image(entry, root)
                if hit:
                    return hit
    # README images can point to a logo outside conventional asset folders.
    for name in ('README.md', 'readme.md', 'README.rst'):
        try:
            readme = root / name
            if not readme.resolve().is_relative_to(root) or not readme.is_file():
                continue
            with readme.open(encoding='utf-8', errors='replace') as stream:
                text = stream.read(32768)
        except (OSError, RuntimeError):
            continue
        for ref in re.findall(r'(?:!\[[^\]]*\]\(\s*<?([^\s)>]+)|<img\b[^>]*\bsrc=[\"\']([^\"\']+))', text, re.I):
            value = next((part for part in ref if part), '')
            if '://' in value or value.startswith('//'):
                continue
            hit = _image(root / value.lstrip('/'), root)
            if hit:
                return hit
    return None


def avatar(repo, state_dir):
    root = Path(repo).resolve()
    override = _override(state_dir, root)
    try:
        saved = json.loads(override.read_text())
        return base64.b64decode(saved['data'], validate=True), saved['type'], 'custom', 'Uploaded image'
    except (OSError, ValueError, KeyError, TypeError):
        pass
    key = str(root)
    with _lock:
        cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < 60:
        hit = cached[1]
    else:
        hit = _discover(root)
        with _lock:
            if len(_cache) > 512:
                _cache.clear()
            _cache[key] = (time.monotonic(), hit)
    if hit:
        data, content_type, source = hit
        return data, content_type, 'automatic', source
    words = re.findall(r'[\w]+', root.name)
    initials = ''.join(word[0] for word in words[:2]).upper() or '?'
    hue = int(hashlib.sha256(key.encode()).hexdigest()[:4], 16) % 360
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
           f'<rect width="64" height="64" rx="14" fill="hsl({hue},45%,36%)"/>'
           f'<text x="32" y="33" dy=".35em" text-anchor="middle" font-family="sans-serif" '
           f'font-size="24" fill="white">{html.escape(initials)}</text></svg>')
    return svg.encode(), 'image/svg+xml', 'initials', 'No local logo found'


def save(repo, state_dir, data_url):
    target = _override(state_dir, Path(repo).resolve())
    if data_url is None:
        target.unlink(missing_ok=True)
    else:
        if not isinstance(data_url, str) or len(data_url) > MAX_IMAGE * 4 // 3 + 256:
            raise ValueError('Choose an image smaller than 2 MB')
        match = re.fullmatch(r'data:(image/(?:png|jpeg|webp|gif|svg\+xml|x-icon|vnd.microsoft.icon));base64,([A-Za-z0-9+/=\s]+)', data_url)
        if not match:
            raise ValueError('Choose a PNG, JPEG, WebP, GIF, SVG or ICO image')
        try:
            raw = base64.b64decode(match[2], validate=True)
        except binascii.Error as exc:
            raise ValueError('Invalid image data') from exc
        if not 0 < len(raw) <= MAX_IMAGE:
            raise ValueError('Choose an image smaller than 2 MB')
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=target.parent, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump({'type': match[1], 'data': match[2]}, stream)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    with _lock:
        _cache.pop(str(Path(repo).resolve()), None)
