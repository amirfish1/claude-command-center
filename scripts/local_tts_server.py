#!/usr/bin/env python3
"""Local neural voice (Kokoro, ONNX) behind a tiny loopback HTTP server.

Run with the venv python from ~/.ccc/local-tts (kokoro-onnx + soundfile
installed there; the dashboard itself stays stdlib-only). CCC starts it on
demand (ccc_server/free_runtime.py) and talks to it over 127.0.0.1 only.

    POST /speak  {"text": "...", "voice": "af_heart"}  ->  audio/wav
    POST /stream {"text": "...", "voice": "af_heart"}  ->  audio/mpeg, sentence by sentence
    GET  /health                                        ->  {"ok": true, "model": ..., "stream": bool, "rev": int, ...}

Requests carry "prio": "fg" (a read the user is listening to) or "bg"
(prepare-ahead). Only one piece is made at a time, and a waiting fg request
always goes before bg, so background work never slows the read being heard.

/stream starts sending after the first sentence is made, so playback begins in a
fraction of the full clip's time; it needs lameenc in the venv (404 without it).
"""
import io
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.path.expanduser(os.environ.get("CCC_LOCAL_TTS_DIR", os.path.join(os.path.expanduser("~"), ".ccc", "local-tts")))
PORT = int(os.environ.get("CCC_LOCAL_TTS_PORT", "3019"))
MAX_CHARS = 2000

# Bumped when the process's runtime contract changes (priority, threads), so
# CCC replaces an older process left running from before an upgrade.
SERVER_REV = 2
# Normal priority: at nice 10 a loaded machine starved the voice for tens of
# seconds. Background work is kept polite by the fg-first lock instead.

import soundfile as sf
from kokoro_onnx import Kokoro


def model_path():
    """CCC_KOKORO_MODEL, else full precision when installed, else int8.

    int8 is a third of the size but several times slower on x86 CPUs without
    VNNI, so the installer puts the fp32 model there.
    """
    explicit = os.environ.get("CCC_KOKORO_MODEL", "").strip()
    if explicit:
        return os.path.expanduser(explicit)
    for name in ("kokoro.fp32.onnx", "kokoro.int8.onnx"):
        if os.path.exists(os.path.join(HOME, name)):
            return os.path.join(HOME, name)
    return os.path.join(HOME, "kokoro.int8.onnx")


def _load():
    threads = os.environ.get("CCC_KOKORO_THREADS", "").strip() or "2"
    try:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, int(threads))
        opts.inter_op_num_threads = 1
        opts.enable_cpu_mem_arena = False
        # ORT's intra-op pool busy-spins between ops by default; on a shared
        # box that kept ~3 cores pegged for minutes per read. Sleep instead.
        opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        sess = ort.InferenceSession(model_path(), opts, providers=["CPUExecutionProvider"])
        return Kokoro.from_session(sess, os.path.join(HOME, "voices.bin"))
    except Exception:
        return Kokoro(model_path(), os.path.join(HOME, "voices.bin"))


_kokoro = _load()
_voices = set(_kokoro.get_voices())


class _PriorityLock:
    """One synthesis at a time; waiting fg requests always go before bg."""

    def __init__(self):
        self._cond = threading.Condition()
        self._busy = False
        self._fg_waiting = 0

    def acquire(self, fg):
        with self._cond:
            if fg:
                self._fg_waiting += 1
                try:
                    while self._busy:
                        self._cond.wait()
                finally:
                    self._fg_waiting -= 1
            else:
                while self._busy or self._fg_waiting:
                    self._cond.wait()
            self._busy = True

    def release(self):
        with self._cond:
            self._busy = False
            self._cond.notify_all()


_SYNTH_LOCK = _PriorityLock()
try:
    import lameenc
except ImportError:  # /speak still works; /stream answers 404 and CCC uses /speak
    lameenc = None

FIRST_PIECE_CHARS = 120
PIECE_CHARS = 300


def split_for_stream(text):
    """Sentence-sized pieces; the first is kept short so audio starts early."""
    sentences = [s for s in re.split(r"(?<=[.!?;:])\s+|\n+", text) if s.strip()]
    pieces, cur = [], ""
    for sent in sentences:
        limit = FIRST_PIECE_CHARS if not pieces else PIECE_CHARS
        while len(sent) > limit:  # one long sentence: cut at the last space
            cut = sent.rfind(" ", 0, limit)
            cut = cut if cut > 20 else limit
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.append(sent[:cut].strip())
            sent = sent[cut:].strip()
            limit = PIECE_CHARS
        if cur and len(cur) + 1 + len(sent) > limit:
            pieces.append(cur)
            cur = sent
        else:
            cur = (cur + " " + sent).strip()
        if len(pieces) == 0 and cur and len(cur) >= FIRST_PIECE_CHARS // 2:
            pieces.append(cur)
            cur = ""
    if cur:
        pieces.append(cur)
    return [p for p in pieces if p.strip()]


def _synth(text, voice, fg=True):
    _SYNTH_LOCK.acquire(fg)
    try:
        return _kokoro.create(text, voice=voice, speed=1.0, lang="en-gb" if voice.startswith("b") else "en-us")
    finally:
        _SYNTH_LOCK.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True, "model": os.path.basename(model_path()), "stream": lameenc is not None,
                             "rev": SERVER_REV, "pid": os.getpid(), "nice": os.nice(0),
                             "threads": int(os.environ.get("CCC_KOKORO_THREADS", "").strip() or "2")})
        else:
            self._json(404, {"ok": False})

    def _request(self):
        length = int(self.headers.get("Content-Length", "0"))
        req = json.loads(self.rfile.read(length)) if 0 < length <= 64 * 1024 else {}
        text = str(req.get("text") or "").strip()[:MAX_CHARS]
        voice = req.get("voice") if req.get("voice") in _voices else "af_heart"
        self._fg = req.get("prio") != "bg"
        return text, voice

    def _stream(self):
        """mp3 frames per piece, written as each piece is made (close-delimited body)."""
        try:
            text, voice = self._request()
        except (ValueError, OSError):
            text, voice = "", ""
        if not text:
            self._json(400, {"ok": False})
            return
        enc = lameenc.Encoder()
        enc.set_bit_rate(64)
        enc.set_in_sample_rate(24000)
        enc.set_channels(1)
        enc.set_quality(5)
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        t0 = time.time()
        try:
            for i, piece in enumerate(split_for_stream(text)):
                samples, _rate = _synth(piece, voice, self._fg)
                pcm = (samples.clip(-1.0, 1.0) * 32767).astype("<i2").tobytes()
                self.wfile.write(bytes(enc.encode(pcm)))
                self.wfile.flush()
                if i == 0:
                    sys.stderr.write("stream first piece %d chars in %dms\n" % (len(piece), (time.time() - t0) * 1000))
            self.wfile.write(bytes(enc.flush()))
        except (BrokenPipeError, ConnectionResetError):
            pass  # the reader stopped (Stop / skip); drop the rest

    def do_POST(self):
        if self.path == "/stream" and lameenc is not None:
            self._stream()
            return
        if self.path != "/speak":
            self._json(404, {"ok": False})
            return
        try:
            text, voice = self._request()
            if not text:
                self._json(400, {"ok": False})
                return
            samples, rate = _synth(text, voice, self._fg)
            buf = io.BytesIO()
            sf.write(buf, samples, rate, format="WAV", subtype="PCM_16")
        except Exception as exc:  # keep serving; the caller falls back
            self._json(500, {"ok": False, "error": type(exc).__name__})
            return
        data = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    _synth("Ready.", "af_heart")  # first inference is slow; pay it before the first Speak
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
