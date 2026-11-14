"""Local speech-to-text for the mobile "hold to record" ticket button.

POST /api/ticket-audio/transcribe  raw audio body (audio/mp4, audio/webm, ...)
    -> {"ok": true, "text": "..."}   or {"ok": false, "error": "..."} (400/413/503)
GET  /api/ticket-audio/status      -> {"ok": true, "available": bool, "reason": ...,
                                       "max_bytes": N, "max_seconds": N}

Everything runs on this machine with ``ffmpeg`` and ``whisper-cli`` (whisper.cpp)
from PATH; no cloud or paid call is made. The model path comes from
``CCC_WHISPER_MODEL`` (default ``~/.cache/whisper/ggml-base.en.bin``).

ffmpeg safety: the upload is written to a private 0700 temp dir, the container is
sniffed from magic bytes and forced with ``-f`` (so playlist/concat demuxers can
never be selected), and ``-protocol_whitelist file`` blocks every network
protocol. Only that one temp file is ever named on the command line.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024
MAX_SECONDS = 120
FFMPEG_TIMEOUT = 30
WHISPER_TIMEOUT = 90
_SLOTS = threading.BoundedSemaphore(1)  # one transcription at a time

_DEFAULT_MODEL = "~/.cache/whisper/ggml-base.en.bin"
_WAV_BYTES_PER_SEC = 16000 * 2


def model_path() -> Path:
    return Path(os.path.expanduser(os.environ.get("CCC_WHISPER_MODEL") or _DEFAULT_MODEL))


def availability() -> tuple[bool, str | None]:
    if not shutil.which("ffmpeg"):
        return False, "ffmpeg is not installed on this machine"
    if not shutil.which("whisper-cli"):
        return False, "whisper-cli (whisper.cpp) is not installed on this machine"
    if not model_path().is_file():
        return False, "Whisper model not found (set CCC_WHISPER_MODEL)"
    return True, None


def sniff_format(data: bytes) -> str | None:
    """Return the ffmpeg demuxer name for a supported container, else None."""
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "mov"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "matroska"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "mp3"
    return None


class AudioError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise AudioError(503, "Transcription timed out; try a shorter recording")
    except OSError as e:
        raise AudioError(503, f"Could not run {cmd[0]}: {e.strerror or e}")


def transcribe(data: bytes) -> str:
    if not data:
        raise AudioError(400, "Empty audio body")
    if len(data) > MAX_BYTES:
        raise AudioError(413, "Recording is larger than 10 MB")
    fmt = sniff_format(data)
    if not fmt:
        raise AudioError(400, "Unsupported audio format (expected mp4/m4a, webm, ogg, wav or mp3)")
    ok, why = availability()
    if not ok:
        raise AudioError(503, why or "Local transcription is unavailable")
    if not _SLOTS.acquire(blocking=False):
        raise AudioError(503, "Another recording is being transcribed; try again in a moment")
    try:
        with tempfile.TemporaryDirectory(prefix="ccc-ticket-audio-") as tmp:
            os.chmod(tmp, 0o700)
            src = os.path.join(tmp, "in.bin")
            wav = os.path.join(tmp, "out.wav")
            fd = os.open(src, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            # -t MAX+1 lets us tell "exactly at the limit" from "over it".
            r = _run(["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file",
                      "-f", fmt, "-i", "file:" + src, "-vn", "-map", "0:a:0",
                      "-t", str(MAX_SECONDS + 1), "-ar", "16000", "-ac", "1",
                      "-c:a", "pcm_s16le", "-f", "wav", "-y", wav], FFMPEG_TIMEOUT)
            if r.returncode != 0 or not os.path.isfile(wav):
                raise AudioError(400, "Could not decode audio")
            seconds = max(0, os.path.getsize(wav) - 44) / _WAV_BYTES_PER_SEC
            if seconds > MAX_SECONDS + 0.5:
                raise AudioError(400, f"Recording is longer than {MAX_SECONDS} seconds")
            if seconds < 0.2:
                raise AudioError(400, "Recording is too short")
            out = os.path.join(tmp, "out")
            r = _run(["whisper-cli", "-m", str(model_path()), "-f", wav, "-nt", "-np",
                      "-l", "en", "-otxt", "-of", out], WHISPER_TIMEOUT)
            if r.returncode != 0:
                raise AudioError(503, "Local transcription failed")
            try:
                text = Path(out + ".txt").read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            return " ".join(text.split())
    finally:
        _SLOTS.release()


def handle(handler, method: str) -> bool:
    """Serve /api/ticket-audio/*; returns True if the request was handled."""
    from urllib.parse import urlparse
    path = urlparse(handler.path).path.rstrip("/")
    if path == "/api/ticket-audio/status" and method == "GET":
        ok, why = availability()
        handler.send_json({"ok": True, "available": ok, "reason": why,
                           "max_bytes": MAX_BYTES, "max_seconds": MAX_SECONDS})
        return True
    if path == "/api/ticket-audio/transcribe" and method == "POST":
        try:
            n = int(handler.headers.get("Content-Length", "") or 0)
        except ValueError:
            handler.send_json({"ok": False, "error": "Invalid Content-Length"}, 400)
            return True
        if n <= 0:
            handler.send_json({"ok": False, "error": "Empty audio body"}, 400)
            return True
        if n > MAX_BYTES:
            handler.send_json({"ok": False, "error": "Recording is larger than 10 MB"}, 413)
            handler.close_connection = True
            return True
        try:
            body = handler.rfile.read(n)
            handler.send_json({"ok": True, "text": transcribe(body)})
        except AudioError as e:
            handler.send_json({"ok": False, "error": e.message}, e.status)
        except OSError:
            handler.send_json({"ok": False, "error": "Could not read upload"}, 400)
        return True
    return False
