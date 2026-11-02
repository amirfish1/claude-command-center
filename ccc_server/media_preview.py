"""Downscaled JPEG previews for large /api/media images (CCC-298).

The dashboard reaches a remote browser over one HTTP/2 connection. On a slow
link one full-size screenshot (~1 MB) holds that connection for seconds and
every other dashboard request waits behind it. `/api/media?preview=1` serves a
JPEG of at most PREVIEW_MAX_BYTES instead; the full file stays one click away.

Stdlib only: the resize shells out to whichever converter the host has
(ImageMagick, ffmpeg, or macOS sips). With none of them, or on any failure,
callers fall back to the original file.
"""

import hashlib
import os
import shutil
import subprocess
import threading
from pathlib import Path

PREVIEW_MAX_BYTES = 150 * 1024
PREVIEW_EXTS = (".png", ".jpg", ".jpeg", ".webp")
PREVIEW_DIR = Path.home() / ".claude" / "command-center" / "media-previews"
_PREVIEW_TIMEOUT_S = 20
_PREVIEW_CACHE_MAX_FILES = 400
# Edge lengths tried in turn until the JPEG fits the byte budget.
_EDGES = (1600, 1200, 900, 640)

_locks_guard = threading.Lock()
_locks = {}


def wants_preview(file_path, size):
    return Path(file_path).suffix.lower() in PREVIEW_EXTS and size > PREVIEW_MAX_BYTES


def _key(file_path, st):
    raw = f"{os.path.realpath(file_path)}|{st.st_mtime_ns}|{st.st_size}"
    return hashlib.sha1(raw.encode("utf-8", "surrogateescape")).hexdigest()


def _commands(src, out, edge):
    s, o = str(src), str(out)
    cmds = []
    magick = shutil.which("magick") or shutil.which("convert")
    if magick:
        cmds.append([
            magick, s + "[0]", "-auto-orient", "-resize", f"{edge}x{edge}>",
            "-background", "white", "-alpha", "remove", "-alpha", "off",
            "-strip", "-quality", "80",
            "-define", f"jpeg:extent={PREVIEW_MAX_BYTES // 1024}kb", "jpeg:" + o,
        ])
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        cmds.append([
            ffmpeg, "-v", "error", "-y", "-i", s, "-frames:v", "1",
            "-vf", f"scale='min({edge},iw)':'min({edge},ih)':force_original_aspect_ratio=decrease",
            "-q:v", "6", "-f", "image2", "-c:v", "mjpeg", o,
        ])
    sips = shutil.which("sips")
    if sips:
        cmds.append([sips, "-s", "format", "jpeg", "-s", "formatOptions", "70",
                     "-Z", str(edge), s, "--out", o])
    return cmds


def _prune(dir_path):
    try:
        files = sorted(dir_path.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    for p in files[:-_PREVIEW_CACHE_MAX_FILES]:
        try:
            p.unlink()
        except OSError:
            pass


def preview_path(file_path, st, cache_dir=None):
    """Return a cached JPEG preview path (<= PREVIEW_MAX_BYTES), or None."""
    cache_dir = Path(cache_dir or PREVIEW_DIR)
    key = _key(file_path, st)
    out = cache_dir / f"{key}.jpg"
    try:
        if out.is_file() and 0 < out.stat().st_size <= PREVIEW_MAX_BYTES:
            return out
    except OSError:
        pass
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        try:
            if out.is_file() and 0 < out.stat().st_size <= PREVIEW_MAX_BYTES:
                return out
            cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        tmp = cache_dir / f"{key}.{os.getpid()}.{threading.get_ident()}.tmp.jpg"
        try:
            for edge in _EDGES:
                for cmd in _commands(file_path, tmp, edge):
                    try:
                        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           timeout=_PREVIEW_TIMEOUT_S, check=False)
                    except (OSError, subprocess.SubprocessError):
                        continue
                    try:
                        n = tmp.stat().st_size if r.returncode == 0 else 0
                    except OSError:
                        n = 0
                    if 0 < n <= PREVIEW_MAX_BYTES:
                        os.replace(tmp, out)
                        _prune(cache_dir)
                        return out
                    if n:
                        break  # converter works; try a smaller edge
            return None
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
            with _locks_guard:
                _locks.pop(key, None)
