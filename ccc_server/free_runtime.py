"""$0 spawn runtime: point one spawned engine at the CCC-managed free router.

The free router (freellmapi, loopback only) is owned by the free-router
subsystem (``ccc_server.free_router``). This module is the spawn-side slice:
given ``runtime: "free"`` on a spawn request, build the env overlay that puts
the child process on $0 models, scrub inherited paid credentials so a session
can never bill the user's plan, and stamp the runtime onto the spawn records
the dashboard reads (registry row, spawn marker, log line).

Contract kept on purpose:
- ``spawn_env(engine, model)`` returns ``{}`` whenever the router cannot
  serve this engine. Callers treat {} as "unavailable" and refuse the spawn:
  a requested $0 run must never silently become a paid one.
- Nothing here writes ``~/.claude/settings.json`` or touches the user's own
  Claude auth. Free routing is per-child env only.

Engines:
- ``claude``: Anthropic endpoint. ``ANTHROPIC_BASE_URL`` +
  ``ANTHROPIC_AUTH_TOKEN`` (+ ``ANTHROPIC_MODEL`` when a model is known).
- ``opencode``/``aider``: OpenAI-compatible ``/v1`` endpoint —
  ``OPENAI_BASE_URL``/``OPENAI_API_BASE`` + ``OPENAI_API_KEY`` and a
  ``<provider>/<model>`` command-line id.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket

from ccc_server import core as _core

FREE_RUNTIME = "free"
FREE_RUNTIME_ENGINES = ("claude", "opencode", "aider")
FREE_RUNTIME_LABEL = "Free ($0)"

# Marker/runtime values are capped like the other spawn-marker fields.
_RUNTIME_VALUE_MAX = 32

# Env vars inherited from the dashboard's own process that must NOT reach a
# free-router child. The router's Anthropic endpoint accepts x-api-key; a
# subscription credential riding along in the environment would bill (or at
# minimum leak through) the paid path the user opted out of.
_PAID_CREDENTIAL_ENV = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_SESSION_KEY",
)

# Advertised to the child's hooks/tools so they can recognise a $0 session
# without re-reading the router state (hooks/session-start.py stamps the
# spawn marker; savings accounting keys off the same value).
RUNTIME_ENV_VAR = "CCC_SESSION_RUNTIME"

_DEFAULT_ROUTER_PORT = 3017
_ROUTER_PROBE_TIMEOUT_S = 0.4


def _state_file():
    override = os.environ.get("CCC_FREE_ROUTER_STATE")
    if override:
        return Path(override)
    # The router owner honors CCC_FREE_ROUTER_HOME for its whole state dir.
    home = os.environ.get("CCC_FREE_ROUTER_HOME", "").strip()
    if home:
        return Path(os.path.expanduser(home)) / "free-router.json"
    return Path.home() / ".ccc" / "free-router.json"


def router_state():
    """The free-router state file as a dict, {} when absent/malformed.

    Written by the router installer with mode 0600: port, version, pinned
    rev, admin account, and the unified inference key. Read-only here —
    this module never provisions, only consumes.
    """
    try:
        data = json.loads(_state_file().read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _state_value(state, *names):
    for name in names:
        value = str(state.get(name) or "").strip()
        if value:
            return value
    return ""


def router_base_url(state=None):
    state = router_state() if state is None else state
    base = _state_value(state, "base_url", "anthropic_base_url", "url")
    if base:
        return base.rstrip("/")
    try:
        port = int(state.get("port") or _DEFAULT_ROUTER_PORT)
    except (TypeError, ValueError):
        port = _DEFAULT_ROUTER_PORT
    return f"http://127.0.0.1:{port}"


def _router_port(state=None):
    state = router_state() if state is None else state
    try:
        return int(state.get("port") or _DEFAULT_ROUTER_PORT)
    except (TypeError, ValueError):
        return _DEFAULT_ROUTER_PORT


def unified_key(state=None):
    """The router's unified inference key, or "" (never logged, never echoed)."""
    state = router_state() if state is None else state
    return _state_value(state, "unified_key", "api_key", "inference_key")


def router_listening(state=None):
    """Cheap TCP probe: is something accepting connections on the router port?

    Deliberately not an HTTP request — a plain connect is enough to tell a
    stopped router from a running one and works before the HTTP stack is
    even answering.
    """
    port = _router_port(state)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=_ROUTER_PROBE_TIMEOUT_S):
            return True
    except OSError:
        return False


def _free_router_module():
    """ccc_server.free_router when that subsystem is present, else None."""
    try:
        from ccc_server import free_router
    except ImportError:
        return None
    return free_router


def _delegate_spawn_env(model):
    """L01's authoritative env builder; {} when the module or readiness is absent."""
    mod = _free_router_module()
    spawn_env = getattr(mod, "spawn_env", None) if mod is not None else None
    if not callable(spawn_env):
        return {}
    try:
        env = spawn_env(model)
    except Exception:
        return {}
    return env if isinstance(env, dict) else {}


def _claude_env_from_state(state, model):
    """Fallback env built straight from the state file, for before/without L01."""
    key = unified_key(state)
    if not key:
        return {}
    env = {
        "ANTHROPIC_BASE_URL": router_base_url(state),
        "ANTHROPIC_AUTH_TOKEN": key,
    }
    resolved = str(model or "").strip() or _state_value(state, "default_model", "model")
    if resolved:
        env["ANTHROPIC_MODEL"] = resolved
    return env


def _openai_env_from_state(state):
    """OpenAI-compatible /v1 env for opencode/aider, from the state file."""
    key = unified_key(state)
    if not key:
        return {}
    base = router_base_url(state) + "/v1"
    return {
        # aider (litellm) reads OPENAI_API_BASE; opencode (AI SDK) reads
        # OPENAI_BASE_URL. Setting both keeps each client on its native var.
        "OPENAI_API_BASE": base,
        "OPENAI_BASE_URL": base,
        "OPENAI_API_KEY": key,
    }


def spawn_env(engine, model=None):
    """Env overlay for a $0 child of ``engine`` — {} when the router can't serve.

    ``model`` is the caller's explicit pick (or None for the router default).
    The returned dict never contains a paid-provider credential and is safe
    to merge over the inherited environment after ``scrub_paid_env``.

    When ``ccc_server.free_router`` (the router owner) is importable its
    ``spawn_env`` is the authoritative can-serve check — it probes /readyz,
    so "up but no upstream can serve" refuses like "down" does. The
    state-file fallback only covers checkouts where the owner is absent.
    """
    engine = str(engine or "").strip().lower()
    if engine not in FREE_RUNTIME_ENGINES:
        return {}
    owner = _free_router_module()
    state = router_state()
    if engine == "claude":
        if owner is not None:
            out = dict(_delegate_spawn_env(model))
        else:
            out = _claude_env_from_state(state, model)
            if out and not router_listening(state):
                return {}
        if not out.get("ANTHROPIC_BASE_URL") or not out.get("ANTHROPIC_AUTH_TOKEN"):
            return {}
    else:
        if owner is not None:
            # The delegate env is Anthropic-shaped; here it is only the
            # can-serve signal. The OpenAI overlay still comes from state.
            if not _delegate_spawn_env(model):
                return {}
            out = _openai_env_from_state(state)
        else:
            out = _openai_env_from_state(state)
            if out and not router_listening(state):
                return {}
    if not out:
        return {}
    out[RUNTIME_ENV_VAR] = FREE_RUNTIME
    return out


def scrub_paid_env(env):
    """Drop inherited paid-provider credentials from a child env in place."""
    for key in _PAID_CREDENTIAL_ENV:
        env.pop(key, None)
    return env


def apply_to_env(env, engine, model=None):
    """Merge the $0 overlay into ``env``; returns True when the overlay applied.

    False means the router cannot serve — caller must refuse the spawn rather
    than proceed paid. Paid credentials are scrubbed regardless of merge
    order so the child's environment never mixes auth sources.
    """
    overlay = spawn_env(engine, model=model)
    if not overlay:
        return False
    scrub_paid_env(env)
    env.update(overlay)
    return True


def model_for(engine, requested=None, env=None):
    """Command-line model for a $0 spawn.

    claude: the explicit pick, else the router-provided ANTHROPIC_MODEL, else
    "auto" (the router's own pick). opencode/aider need an OpenAI-style
    ``openai/<id>`` — "auto" means the router chooses the free model.
    """
    engine = str(engine or "").strip().lower()
    requested = str(requested or "").strip()
    if engine == "claude":
        if requested:
            return requested
        env_model = str((env or {}).get("ANTHROPIC_MODEL") or "").strip()
        return env_model or "auto"
    if engine in ("opencode", "aider"):
        target = requested or _default_router_model() or "auto"
        return target if "/" in target else f"openai/{target}"
    return requested


def _default_router_model():
    state = router_state()
    return _state_value(state, "default_model", "model")


# free_router.status() lifecycle states -> novice-facing refusal reason.
_OWNER_STATE_REASONS = {
    "missing": "the free router is not installed yet",
    "stopped": "the free router is not running",
    "starting": "the free router is still starting up",
    "needs_setup": "the free router needs its setup finished",
    "needs_key": "the free router has no free-model key yet",
    "degraded": "the free router is up but no free model can serve yet",
}


def readiness(engine):
    """(ready, reason) for the free runtime on ``engine`` — cheap, spawn-safe.

    With the router owner importable, its ``status()`` answer is
    authoritative (cached ~2.5s there). Without it, a state-file read plus
    one loopback TCP probe stands in.
    """
    engine = str(engine or "").strip().lower()
    if engine not in FREE_RUNTIME_ENGINES:
        return False, f"the free runtime does not support the {engine or '?'} engine"
    owner = _free_router_module()
    if owner is not None:
        try:
            st = owner.status() or {}
        except Exception:
            st = {}
        if st.get("ready"):
            return True, ""
        reason = str(st.get("ready_reason") or "").strip()
        if not reason:
            reason = _OWNER_STATE_REASONS.get(
                str(st.get("state") or ""), "the free router is not ready")
        return False, reason
    state = router_state()
    if not state:
        return False, "the free router is not installed yet"
    if not unified_key(state):
        return False, "the free router has no inference key yet"
    if not router_listening(state):
        return False, "the free router is not running"
    return True, ""


def unavailable_result(engine, reason=""):
    """Spawn-result shape for a refused $0 request — never a silent fallback."""
    engine = str(engine or "").strip().lower()
    ready, why = readiness(engine)
    detail = reason or why or "the free router is not ready"
    return {
        "ok": False,
        "error": (
            "This session was asked to run free ($0), but "
            + detail
            + ". Start the free router in Settings > Free models, or send again without runtime=free."
        ),
        "code": "free_runtime_unavailable",
        "engine": engine,
        "runtime": FREE_RUNTIME,
    }


def normalize_runtime(value):
    """The payload runtime token: "free", "", or an unknown value to reject."""
    runtime = str(value or "").strip().lower()
    return runtime


def served_by(input_tokens):
    """(provider, model) that last answered a request of this exact size.

    The router logs no session id, but a Claude Code turn's input token count
    is the same number in the transcript and in the router's request log, so
    the latest turn's count finds the router row. Latest successful row in the
    last day wins; ("", "") when nothing matches or the log is unreadable.
    """
    try:
        n = int(input_tokens or 0)
    except (TypeError, ValueError):
        return "", ""
    if n <= 0:
        return "", ""
    db = _state_file().parent / "freellmapi" / "server" / "data" / "freeapi.db"
    if not db.is_file():
        return "", ""
    try:
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=0.5)
        try:
            row = con.execute(
                "SELECT platform, model_id FROM requests WHERE input_tokens=? "
                "AND status='success' AND created_at > datetime('now','-1 day') "
                "ORDER BY id DESC LIMIT 1", (n,)).fetchone()
        finally:
            con.close()
    except Exception:
        return "", ""
    return (str(row[0]), str(row[1])) if row else ("", "")


def served_map(limit=400):
    """{input_tokens: info} for the router's recent requests, newest wins.

    info: provider, model, latency_ms, ttfb_ms, failed (error attempts at the
    same size in the 10 minutes before the success: the failover chain).
    Keyed by input size because the router logs no session id (see served_by).
    """
    db = _state_file().parent / "freellmapi" / "server" / "data" / "freeapi.db"
    if not db.is_file():
        return {}
    try:
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=0.5)
        try:
            rows = con.execute(
                "SELECT input_tokens, platform, model_id, status, latency_ms, ttfb_ms, "
                "CAST(strftime('%s', created_at) AS INTEGER) FROM requests "
                "WHERE created_at > datetime('now','-2 day') AND input_tokens > 0 "
                "ORDER BY id DESC LIMIT ?", (int(limit) * 4,)).fetchall()
        finally:
            con.close()
    except Exception:
        return {}
    out = {}
    for tok, plat, model, status, lat, ttfb, ts in rows:
        if status == "success" and str(tok) not in out and len(out) < limit:
            out[str(tok)] = {"provider": plat, "model": model, "latency_ms": lat,
                             "ttfb_ms": ttfb, "failed": 0, "_ts": ts}
    for tok, plat, model, status, lat, ttfb, ts in rows:
        info = out.get(str(tok))
        if info and status != "success" and 0 <= info["_ts"] - ts <= 600:
            info["failed"] += 1
    for info in out.values():
        info.pop("_ts", None)
    return out


TTS_VOICES = (
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
)
# Cloud fallbacks, tried in order after the local Kokoro voice (see tts()):
# Gemini (30 voices), then Cloudflare MeloTTS when Google rate-limits the free
# tier. Aura is left out: the router sends it the wrong field name and
# Cloudflare rejects it.
TTS_MODELS = ("gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts", "@cf/myshell-ai/melotts")
TTS_MAX_CHARS = 2000


def _audio_type(data):
    """Content type from the bytes: the router labels MeloTTS WAV as mpeg."""
    if data[:4] == b"RIFF":
        return "audio/wav"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    return "application/octet-stream"


# English Kokoro voices (American "a", British "b"; f/m = female/male).
KOKORO_VOICES = (
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole",
    "af_nova", "af_river", "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir",
    "am_liam", "am_michael", "am_onyx", "am_puck", "am_santa", "bf_alice", "bf_emma",
    "bf_isabella", "bf_lily", "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
)
_KOKORO_LABEL = "Kokoro: "

_LOCAL_TTS_DIR = Path.home() / ".ccc" / "local-tts"
_LOCAL_TTS_PORT = int(os.environ.get("CCC_LOCAL_TTS_PORT", "3019"))
_LOCAL_TTS_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "local_tts_server.py"


def _local_tts_up():
    try:
        with socket.create_connection(("127.0.0.1", _LOCAL_TTS_PORT), timeout=0.3):
            return True
    except OSError:
        return False


def local_tts_installed():
    py = _LOCAL_TTS_DIR / "venv" / "bin" / "python"
    model = any((_LOCAL_TTS_DIR / name).exists() for name in ("kokoro.fp32.onnx", "kokoro.int8.onnx"))
    return py.exists() and model and (_LOCAL_TTS_DIR / "voices.bin").exists()


def _local_tts_start():
    """Start the loopback Kokoro server detached; True once it answers."""
    import subprocess
    import time
    if _local_tts_up():
        return True
    if not local_tts_installed():
        return False
    subprocess.Popen(
        [str(_LOCAL_TTS_DIR / "venv" / "bin" / "python"), str(_LOCAL_TTS_SCRIPT)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    for _ in range(40):
        time.sleep(0.25)
        if _local_tts_up():
            return True
    return False


def _kokoro_voice(voice):
    """A Kokoro voice name from a label or name; blank/unknown picks a random one."""
    import random
    voice = str(voice or "")
    if voice.startswith(_KOKORO_LABEL):
        voice = voice[len(_KOKORO_LABEL):]
    return voice if voice in KOKORO_VOICES else random.choice(KOKORO_VOICES)


def _local_tts_request(path, text, voice):
    import urllib.request
    return urllib.request.Request(
        "http://127.0.0.1:%d%s" % (_LOCAL_TTS_PORT, path),
        data=json.dumps({"text": text, "voice": voice}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")


def local_tts_open(text, voice=""):
    """(open HTTP response, label) streaming Kokoro mp3, or (None, "") when unavailable.

    The local server makes the clip a sentence at a time and sends each one as
    soon as it is ready, so playback starts after the first sentence instead of
    after the whole text.
    """
    import urllib.request
    text = str(text or "").strip()[:TTS_MAX_CHARS]
    if not text or not _local_tts_start():
        return None, ""
    voice = _kokoro_voice(voice)
    try:
        return urllib.request.urlopen(_local_tts_request("/stream", text, voice), timeout=60), _KOKORO_LABEL + voice
    except Exception:  # older venv without lameenc answers 404: caller uses local_tts()
        return None, ""


def local_tts(text, voice=""):
    """(audio, label) from the local Kokoro voice, or (b"", "") when unavailable.

    A blank or unknown voice picks a random one; the label ("Kokoro: af_nova")
    round-trips through the browser so one read keeps one voice.
    """
    import urllib.request
    voice = _kokoro_voice(voice)
    if not _local_tts_start():
        return b"", ""
    req = _local_tts_request("/speak", text, voice)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        return (data, _KOKORO_LABEL + voice) if data else (b"", "")
    except Exception:
        return b"", ""


# Deepgram Aura-2: optional metered voice, off unless explicitly turned on with
# CCC_DEEPGRAM=1 or an ~/.ccc/deepgram.on file. The key lives outside the repo
# (DEEPGRAM_API_KEY or ~/.ccc/deepgram.key); a key alone does not enable it.
DEEPGRAM_VOICES = (
    "andromeda", "apollo", "arcas", "aries", "asteria", "athena", "atlas", "aurora",
    "callista", "cora", "cordelia", "delia", "draco", "electra", "harmonia", "helena",
    "hera", "hermes", "hyperion", "iris", "janus", "juno", "jupiter", "luna", "mars",
    "minerva", "neptune", "odysseus", "ophelia", "orion", "orpheus", "pandora",
    "phoebe", "pluto", "saturn", "thalia", "theia", "vesta", "zeus",
)
_DEEPGRAM_LABEL = "Deepgram: "


def deepgram_enabled():
    flag = os.environ.get("CCC_DEEPGRAM", "").strip()
    if flag:
        return flag == "1"
    return (Path.home() / ".ccc" / "deepgram.on").exists()


def _deepgram_key():
    if not deepgram_enabled():
        return ""
    key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    if key:
        return key
    try:
        return (Path.home() / ".ccc" / "deepgram.key").read_text().strip()
    except OSError:
        return ""


def deepgram_open(text, voice="", fmt="encoding=linear16&container=wav"):
    """(open HTTP response, label) from Deepgram Aura-2, or (None, "") when off or failing.

    The response is returned as soon as headers arrive (about 0.3 s); the caller
    reads the audio body, so it can be relayed while Deepgram is still making it.
    """
    import random
    import urllib.request
    key = _deepgram_key()
    text = str(text or "").strip()[:TTS_MAX_CHARS]
    if not key or not text:
        return None, ""
    voice = str(voice or "")
    if voice.startswith(_DEEPGRAM_LABEL):
        voice = voice[len(_DEEPGRAM_LABEL):]
    if voice not in DEEPGRAM_VOICES:
        voice = random.choice(DEEPGRAM_VOICES)
    req = urllib.request.Request(
        "https://api.deepgram.com/v1/speak?model=aura-2-%s-en&%s" % (voice, fmt),
        data=json.dumps({"text": text}).encode(),
        headers={"Authorization": "Token " + key, "Content-Type": "application/json"},
        method="POST")
    try:
        return urllib.request.urlopen(req, timeout=30), _DEEPGRAM_LABEL + voice
    except Exception:
        return None, ""


def deepgram_tts(text, voice=""):
    """(audio, label) from Deepgram Aura-2 as a whole WAV, or (b"", "")."""
    resp, label = deepgram_open(text, voice)
    if resp is None:
        return b"", ""
    try:
        with resp:
            data = resp.read()
        return (data, label) if data[:4] == b"RIFF" else (b"", "")
    except Exception:
        return b"", ""


# Google's free TTS quota is small and daily. After a 429 the Gemini models are
# skipped for a while so each Speak does not pay two failed round trips first.
_GEMINI_COOLDOWN_S = 900
_gemini_blocked_until = 0.0


def tts(text, voice=""):
    """Speak ``text``: (status, audio, content_type, label).

    Order: Deepgram when explicitly enabled, else the local Kokoro voice, then
    the free router (Gemini, MeloTTS). A blank or unknown voice picks a random
    one, so repeated reads sample the catalog. label names what spoke
    ("Kokoro: af_nova", "Puck", "MeloTTS"). status is the HTTP status to
    relay; audio is empty on failure. The router key stays on this side of the
    loopback.
    """
    import random
    import urllib.request
    text = str(text or "").strip()[:TTS_MAX_CHARS]
    if not text:
        return 400, b"", "", ""
    dg, dg_label = deepgram_tts(text, voice)
    if dg:
        return 200, dg, _audio_type(dg), dg_label
    kokoro_voice = voice if str(voice).startswith(_KOKORO_LABEL) else ""
    cloud_voice = voice in TTS_VOICES or voice == "MeloTTS"
    if not cloud_voice and local_tts_installed():
        # Local and unmetered, so it goes first. A read that started on a cloud
        # voice (Kokoro was down) keeps that voice instead of switching mid-read.
        local, label = local_tts(text, kokoro_voice)
        if local:
            return 200, local, _audio_type(local), label
    voice = voice if voice in TTS_VOICES else random.choice(TTS_VOICES)
    key = unified_key()
    status = 502
    if not key or not router_listening():
        return 503, b"", "", voice
    import time
    global _gemini_blocked_until
    for model in TTS_MODELS:
        melo = model.startswith("@cf/")
        if model.startswith("gemini") and time.time() < _gemini_blocked_until:
            continue
        body = {"model": model, "input": text}
        if not melo:
            body["voice"] = voice
        req = urllib.request.Request(
            router_base_url() + "/v1/audio/speech", data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            if data:
                return 200, data, _audio_type(data), "MeloTTS" if melo else voice
        except urllib.error.HTTPError as e:
            if e.code == 429 and model.startswith("gemini"):
                _gemini_blocked_until = time.time() + _GEMINI_COOLDOWN_S
            status = 429 if (e.code == 429 or status == 429) else 502
        except Exception:
            status = 502
    return status, b"", "", voice


def session_runtime(session_id):
    """The recorded runtime for a known session id ("free" or "").

    Spawn markers are the durable, post-restart source — a session resumed
    days later still reports the runtime it was born with.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return ""
    try:
        markers = _core._load_spawn_markers()
    except Exception:
        markers = {}
    marker = markers.get(sid) or {}
    return str(marker.get("runtime") or "").strip()[:_RUNTIME_VALUE_MAX]


def mark_spawned(session_id, *, pid=None):
    """Stamp runtime=free everywhere a spawn row is read from.

    Order matters: the marker file is the archive/transcript overlay, the
    registry tag covers /api/sessions/spawned rows, and the in-memory entry
    covers the dashboard's live list before the next poll.
    """
    sid = str(session_id or "").strip()
    try:
        if sid:
            # Merge, not replace: the session-start hook writes caller/parent
            # into the same file and must not lose this stamp (or vice versa).
            _core._merge_spawn_marker(sid, runtime=FREE_RUNTIME)
    except Exception:
        pass
    try:
        _core._tag_spawn_runtime_in_registry(
            pid=pid, session_id=sid or None, runtime=FREE_RUNTIME,
        )
    except Exception:
        pass


def spawn_log_marker_event(model=""):
    """One stream-json line for the head of a $0 session's spawn log."""
    return {
        "type": "system",
        "subtype": "ccc_runtime",
        "runtime": FREE_RUNTIME,
        "model": str(model or ""),
        "content": "This session runs on a free model ($0) via the CCC free router.",
    }
