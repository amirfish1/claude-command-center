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
# English speaker voices from the Apache-2.0 phonon-voices catalog, pinned by revision and hash.
# Each is ~256 KB and is fetched to this host only when a browser first picks it.
VOICES_REVISION = "0ee17205f254b5abc43b266c8a0f215565c352f7"
VOICES_BASE = f"https://huggingface.co/gradium/phonon-voices/resolve/{VOICES_REVISION}/voices/"
VOICES = (  # (id, name, accent, gender, size, sha256)
    ("nGzMHMPAWGaJFHOo", "Delia", "US", "female", 256136, "a0c3dcb91b2247eca56536be9ae8e8becc1c807b1136c5fe101552d6a155b24a"),
    ("vjFyp2BcCyTlUmYb", "Everly", "US", "female", 256136, "958ea94da14f46dd48c7f64adac11ed2ad2aa45f7ccd165dfebab5272664402e"),
    ("bXwrkHhURL1VFJdG", "Hadley", "US", "female", 256136, "a879938aa9a2df0a8220bdadef88137929f71e887bd91e7db205bd5fa11661d2"),
    ("4SZHfMpw-p46Ywgs", "Harper", "US", "female", 256136, "6b3da6e2d796a20c51af892c482bd6509fb88267a221aff83c870843dee2a123"),
    ("NIWIOv5vTi5eRMj8", "Juniper", "US", "female", 256136, "444378eb06b7c492b86e8fc7d13e4db1a3c0cf3e68891a960bf6f0a41a13ef35"),
    ("NbpkqMVS3CJeq2j8", "Zoey", "US", "female", 256136, "97c7f8c253413574406ab80dd23f49c3ebb2517163b00a9bf86b9c4cf43e7e23"),
    ("LMDIYE0Oa377Qh6Z", "Easton", "US", "male", 256136, "45c582673cb20edb04ddcca2b5a8865ddfb0a82599efadb269021e87d20fbdd4"),
    ("40QrMVgAxFiUnP9f", "Micah", "US", "male", 256136, "0de1098d1051686a138d068e83ff09deb5053328bc74ec9541a1fdb90bfc24da"),
    ("d9XCyhjo4JhkHF1d", "Miles", "US", "male", 256136, "eab20e4f159b48cfbc01b1885804e24f681f80e7a64f909182763a4e3361bee8"),
    ("qIxlZ7Ei8KvWlyQH", "Preston", "US", "male", 256136, "6e04b84d3ae8751b22bca0a4f63936f9cb648f4f2182b2d0788071ea1c0b68a1"),
    ("_6Aslh2DxfmnRLmP", "Russell", "US", "male", 256136, "beae9e4030efce0a935fcb0f807dc0784d598bcaa87ad4aacb047436b1eaa411"),
    ("0zLApsZfVLLNaEGl", "Trevor", "US", "male", 256136, "dc1341c8937af5534e6bbcc5c0fc5e7c10312229b59061228074f93683fe2261"),
    ("gfP44RlHwr2kEwk7", "Bryony", "UK", "female", 256136, "bcc207a73b7885e45b49ab633d3377a10158292e3a7cef9cd6764ad107f1cb9b"),
    ("y9bQqIDnyxwqK01t", "Elodie Rose", "UK", "female", 256136, "21eade9d83b2e66b9364e1b6cd999a3a6704c000a71f27f34e05ace5cdfd58bb"),
    ("GgfEkEJtxZR7gnpy", "Freya", "UK", "female", 256136, "eb3cd47418349e618408c16bea7153988b3ad04b41a6033039dd474a9a4dcd23"),
    ("gDT1nz7Ie36ZhL-C", "Imogen", "UK", "female", 256136, "1980ab3c35fa98a25b5f3b5e643fd852e14ca8a12a4a26b06aaf1abf2a8c68b1"),
    ("m9MxpY6IUGXtWp76", "Lottie", "UK", "female", 256136, "a906ebf17e43fded12b3d6bd077abb2df33bc86eff51c1beeb82e5d416527db3"),
    ("6PWnV0Nq4wu7RVBT", "Maeve", "UK", "female", 256136, "56494ee67d533ad227ff0dfd0b1c13ff5aa91f1ad6e596e66c2986ba3005e844"),
    ("uem82D50GRv2Dwma", "Pippa", "UK", "female", 256136, "d41a8f01d6e33450f1ce062d332914da10beee99e585dab531750706f187c48a"),
    ("X6NceqF285Gr04gb", "Alfie", "UK", "male", 256136, "4de75497fff3030168e8aa6dca23ffeb332b1bc8c25d0bf3507ceadac953ae9c"),
    ("zUCxzrOrugTSis03", "Edwin", "UK", "male", 256136, "ff1d0735b8b45710462b1d1f846da040f970c6eb62c7c2f36dbfb8e5bde74f1e"),
    ("YyKcxPG7iIgS3jsK", "Hugh", "UK", "male", 256136, "3169d74b47a54d1eb15e4c243f56393abe9fe90c7e1c308ecf96ee67618b152a"),
    ("JOW0vchyswkQUMDS", "Spencer", "UK", "male", 256136, "cc9ac1107b0ffce282374a19beb7bfa95847724e6b7afbac991b4ae4277d5e6d"),
    ("Zn7g6NxLqXlJb7Qi", "Teddy", "UK", "male", 256136, "baea0ea9506f3237a5ebfc61afb7c72dbd189c5603b199ef42fbb00dbda379e0"),
    ("dME3IWyZBvmh1n1q", "Toby", "UK", "male", 256136, "09ab315c7f4b72b4d71e66cb756184cedfb1f14d222d096e115bf95bfe7b29dc"),
)
_VOICE_INDEX = {v[0]: v for v in VOICES}
_voice_lock = threading.Lock()
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
    }, "voices": [
        {"id": vid, "label": f"{name} · {accent}", "gender": gender,
         "url": f"/api/browser-voice/voices/{VOICES_REVISION[:12]}/{vid}.safetensors", "size": size, "sha256": digest}
        for vid, name, accent, gender, size, digest in VOICES
    ]}


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


def _voice_file(vid):
    """Return the cached, hash-verified speaker file, fetching it once on first use."""
    spec = _VOICE_INDEX.get(vid)
    if not spec:
        return None
    folder = _root() / f"voices-{VOICES_REVISION[:12]}"
    target = folder / f"{vid}.safetensors"
    with _voice_lock:
        if not (target.is_file() and target.stat().st_size == spec[4]):
            folder.mkdir(parents=True, exist_ok=True)
            partial = folder / f".{vid}.part"
            _download(VOICES_BASE + f"{vid}.safetensors", partial, spec[5], spec[4], spec[4])
            partial.replace(target)
    return target


def handle(handler, method):
    """Called after the dashboard's origin, proxy-secret and PIN checks."""
    path = urllib.parse.urlparse(handler.path).path
    if path == "/api/browser-voice/status" and method == "GET":
        handler.send_json(status())
    elif path == "/api/browser-voice/install" and method == "POST":
        handler.send_json(install(), 202)
    elif path.startswith(f"/api/browser-voice/voices/{VOICES_REVISION[:12]}/") and method == "GET":
        name = path.rsplit("/", 1)[-1]
        vid = name[:-len(".safetensors")] if name.endswith(".safetensors") else ""
        if vid not in _VOICE_INDEX or not _installed():
            handler.send_json({"ok": False, "error": "Unknown voice"}, 404)
            return
        try:
            target = _voice_file(vid)
        except Exception as error:
            handler.send_json({"ok": False, "error": "Voice download failed: " + str(error)[:200]}, 502)
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Length", str(target.stat().st_size))
        handler.send_header("Cache-Control", "public, max-age=31536000, immutable")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        with target.open("rb") as source:
            shutil.copyfileobj(source, handler.wfile)
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
