"""Optional, hash-pinned browser voice assets. No downloads before explicit install."""

import hashlib
import json
import mimetypes
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import threading
import urllib.parse
import urllib.request


PACK = "phonon-0.7.1-92741c5b"
REVISION = "92741c5b07d085ff840e26fea8ce43110b87852f"
RUNTIME_URL = "https://registry.npmjs.org/phonon-tts/-/phonon-tts-0.7.1.tgz"
RUNTIME_SHA256 = "f2c936ad4e0caeb60afd9b704157708eb2b32ba361ff4c29e0c3960e87de4f88"
MODEL_BASE = f"https://huggingface.co/gradium/phonon-31m-en/resolve/{REVISION}/"
MODEL_FILES = {
    "model.q8.gguf": (64512352, "a7d9aad2cd6dc66473f24a08bc6d2939030ef4d751ef82349361fbef3272dbae"),
    "config.json": (4634, "b5d0f88353638370d94035b7d73d7d6577c0554d78f8d7ecfd40490aae39ae85"),
    "tokenizer.json": (277967, "691a00068fa7b989f2f3e5ae5daa2d1a5bb319f2d5360deafcfb19a8bb6e7936"),
    "default-voice.safetensors": (256136, "dc537478eaadc1a5257fcda2e3501ab81e14c111a5d95a5838f350db2afd0f1a"),
    "README.md": (10478, "fc04467c0e5560e89cd5bbded052e9b7f6c23e2abcdd03a32a3a07a27a7ea3ed"),
}
RUNTIME_FILES = (
    "wasm/phonon_tts.js", "wasm/phonon_tts_bg.wasm", "wasm/phonon_tts_simd_bg.wasm",
    "simd.js", "LICENSE-MIT", "LICENSE-APACHE", "THIRD_PARTY_LICENSES.md",
)
ASSETS = frozenset(["runtime/" + name for name in RUNTIME_FILES]
                   + ["model/" + name for name in MODEL_FILES])
_lock = threading.Lock()
_state = {"phase": "idle", "file": "", "loaded": 0, "error": ""}


def _root():
    return Path(os.environ.get("CCC_BROWSER_VOICE_DIR", str(Path.home() / ".ccc" / "browser-voice")))


def manifest():
    base = f"/api/browser-voice/assets/{PACK}/"
    return {"pack": PACK, "base": base, "files": {
        name: {"url": base + "model/" + name, "size": size, "sha256": digest}
        for name, (size, digest) in MODEL_FILES.items() if name != "README.md"
    }}


def _installed():
    root = _root() / PACK
    try:
        inventory = json.loads((root / "ready.json").read_text())
        return isinstance(inventory, dict) and isinstance(inventory.get("sizes"), dict) and inventory.get("pack") == PACK and all(
            (root / name).is_file() and (root / name).stat().st_size == inventory["sizes"][name]
            for name in ASSETS)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def status():
    with _lock:
        result = dict(_state)
    result.update(ok=True, installed=_installed(), manifest=manifest())
    return result


def _download(url, target, digest, limit, expected_size=None):
    checksum = hashlib.sha256()
    count = 0
    request = urllib.request.Request(url, headers={"User-Agent": "CCC-browser-voice/1"})
    with urllib.request.urlopen(request, timeout=90) as source, target.open("wb") as output:
        while chunk := source.read(256 * 1024):
            count += len(chunk)
            if count > limit:
                raise ValueError("Voice asset exceeds its pinned size")
            output.write(chunk)
            checksum.update(chunk)
            with _lock:
                _state.update(file=target.name, loaded=count)
    if checksum.hexdigest() != digest or (expected_size is not None and count != expected_size):
        raise ValueError("Voice asset integrity check failed: " + target.name)


def _unpack_runtime(archive, destination):
    """Extract only known regular files; never trust tar paths or links."""
    with tarfile.open(archive, "r:gz") as bundle:
        for name in RUNTIME_FILES:
            member = bundle.getmember("package/" + name)
            if not member.isfile() or member.size > 16 * 1024 * 1024:
                raise ValueError("Invalid voice runtime member")
            target = destination / "runtime" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _install():
    try:
        root = _root()
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="install-", dir=root) as tmp:
            stage = Path(tmp)
            archive = stage / "runtime.tgz"
            _download(RUNTIME_URL, archive, RUNTIME_SHA256, 20 * 1024 * 1024)
            _unpack_runtime(archive, stage)
            archive.unlink()
            (stage / "model").mkdir()
            for name, (size, digest) in MODEL_FILES.items():
                _download(MODEL_BASE + name, stage / "model" / name, digest, size, size)
            inventory = {"pack": PACK, "sizes": {name: (stage / name).stat().st_size for name in ASSETS}}
            (stage / "ready.json").write_text(json.dumps(inventory))
            destination = root / PACK
            if destination.exists():
                shutil.rmtree(destination)
            stage.rename(destination)
        with _lock:
            _state.update(phase="ready", file="", error="")
    except Exception as error:
        with _lock:
            _state.update(phase="error", error=str(error)[:300])


def install():
    with _lock:
        if _state["phase"] != "installing" and not _installed():
            _state.update(phase="installing", file="", loaded=0, error="")
            threading.Thread(target=_install, name="browser-voice-install", daemon=True).start()
    return status()


def handle(handler, method):
    """Called after the dashboard's origin, proxy-secret and PIN checks."""
    path = urllib.parse.urlparse(handler.path).path
    if path == "/api/browser-voice/status" and method == "GET":
        handler.send_json(status())
    elif path == "/api/browser-voice/install" and method == "POST":
        handler.send_json(install(), 202)
    elif path.startswith(f"/api/browser-voice/assets/{PACK}/") and method == "GET":
        name = path[len(f"/api/browser-voice/assets/{PACK}/"):]
        root = (_root() / PACK).resolve()
        target = (root / name).resolve()
        if name not in ASSETS or not target.is_relative_to(root) or not _installed():
            handler.send_json({"ok": False, "error": "Unknown voice asset"}, 404)
            return
        kind = "application/javascript" if name.endswith(".js") else mimetypes.guess_type(name)[0]
        handler.send_response(200)
        handler.send_header("Content-Type", kind or "application/octet-stream")
        handler.send_header("Content-Length", str(target.stat().st_size))
        handler.send_header("Cache-Control", "public, max-age=31536000, immutable")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        with target.open("rb") as source:
            shutil.copyfileobj(source, handler.wfile)
    else:
        handler.send_json({"ok": False, "error": "Unknown browser voice route"}, 404)
