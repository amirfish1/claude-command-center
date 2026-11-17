"""Integrity and filesystem boundary checks for the optional voice pack."""
import hashlib
import io
import json
import tarfile
from unittest.mock import patch

import pytest

from ccc_server import browser_voice as voice


def test_status_never_downloads_or_installs(tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_BROWSER_VOICE_DIR", str(tmp_path / "not-created"))
    with patch.object(voice.urllib.request, "urlopen", side_effect=AssertionError("unexpected download")):
        result = voice.status()
    assert result["installed"] is False
    assert not (tmp_path / "not-created").exists()
    assert result["manifest"]["files"]["model.q8.gguf"]["size"] == 64512352


@pytest.mark.parametrize("payload,limit,digest,size", [
    (b"corrupt", 100, hashlib.sha256(b"correct").hexdigest(), None),
    (b"oversized", 3, hashlib.sha256(b"oversized").hexdigest(), None),
    (b"short", 100, hashlib.sha256(b"short").hexdigest(), 9),
])
def test_download_refuses_corruption_and_size_mismatches(tmp_path, payload, limit, digest, size):
    with patch.object(voice.urllib.request, "urlopen", return_value=io.BytesIO(payload)):
        with pytest.raises(ValueError):
            voice._download("https://example.test/asset", tmp_path / "asset", digest, limit, size)


def test_tar_links_and_unlisted_members_cannot_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "RUNTIME_FILES", ("wasm/phonon_tts.js",))
    archive = tmp_path / "runtime.tgz"
    with tarfile.open(archive, "w:gz") as bundle:
        member = tarfile.TarInfo("package/wasm/phonon_tts.js")
        member.type = tarfile.SYMTYPE
        member.linkname = "../../../outside"
        bundle.addfile(member)
    with pytest.raises(ValueError):
        voice._unpack_runtime(archive, tmp_path / "stage")
    assert not (tmp_path / "outside").exists()


def test_asset_route_refuses_traversal_unknowns_and_outside_symlinks(tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_BROWSER_VOICE_DIR", str(tmp_path))
    monkeypatch.setattr(voice, "_installed", lambda: True)
    root = tmp_path / voice.PACK
    (root / "runtime").mkdir(parents=True)
    (tmp_path / "secret").write_text("private")
    (root / "runtime/simd.js").symlink_to(tmp_path / "secret")

    class Handler:
        def send_json(self, data, status=200):
            self.status = status

    for name in ["../../secret", "%2e%2e/secret", "ready.json", "runtime/simd.js", "model/other.gguf"]:
        handler = Handler()
        handler.path = f"/api/browser-voice/assets/{voice.PACK}/" + name
        voice.handle(handler, "GET")
        assert handler.status == 404


def test_successful_install_is_atomic_and_keeps_only_pinned_files(tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_BROWSER_VOICE_DIR", str(tmp_path / "cache"))
    runtime = io.BytesIO()
    with tarfile.open(fileobj=runtime, mode="w:gz") as bundle:
        for name in ["package/runtime.js", "../../unlisted"]:
            member = tarfile.TarInfo(name)
            member.size = 2
            bundle.addfile(member, io.BytesIO(b"ok"))
    archive = runtime.getvalue()
    monkeypatch.setattr(voice, "RUNTIME_FILES", ("runtime.js",))
    monkeypatch.setattr(voice, "RUNTIME_SHA256", hashlib.sha256(archive).hexdigest())
    monkeypatch.setattr(voice, "MODEL_FILES", {"test.bin": (2, hashlib.sha256(b"ok").hexdigest())})
    monkeypatch.setattr(voice, "ASSETS", frozenset(["runtime/runtime.js", "model/test.bin"]))
    def request(req, **kwargs):
        return io.BytesIO(archive if req.full_url == voice.RUNTIME_URL else b"ok")
    with patch.object(voice.urllib.request, "urlopen", side_effect=request):
        voice._install()
    assert voice._installed()
    assert not (tmp_path / "unlisted").exists()
    root = tmp_path / "cache" / voice.PACK
    assert sorted(p.name for p in root.iterdir()) == ["model", "ready.json", "runtime"]
    inventory = json.loads((root / "ready.json").read_text())
    assert set(inventory["sizes"]) == voice.ASSETS


def test_failed_install_never_publishes_partial_pack(tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_BROWSER_VOICE_DIR", str(tmp_path))
    with patch.object(voice.urllib.request, "urlopen", return_value=io.BytesIO(b"bad archive")):
        voice._install()
    assert voice.status()["phase"] == "error"
    assert not voice._installed()
    assert list(tmp_path.iterdir()) == []


def test_speaker_voices_are_pinned_listed_and_fetched_once_on_demand(tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_BROWSER_VOICE_DIR", str(tmp_path))
    monkeypatch.setattr(voice, "_installed", lambda: True)
    listed = voice.manifest()["voices"]
    assert len(listed) == len(voice.VOICES) >= 20
    assert all(v["size"] == 256136 and len(v["sha256"]) == 64 for v in listed)
    vid = voice.VOICES[0][0]
    payload = b"v" * 4
    monkeypatch.setattr(voice, "_VOICE_INDEX", {vid: (vid, "Test", "US", "female", 4, hashlib.sha256(payload).hexdigest())})
    calls = []

    class Handler:
        def __init__(self, path):
            self.path, self.wfile, self.headers = path, io.BytesIO(), {}
        def send_json(self, data, status=200):
            self.status = status
        def send_response(self, status):
            self.status = status
        def send_header(self, key, value):
            self.headers[key] = value
        def end_headers(self):
            pass

    def fetch(req, **kwargs):
        calls.append(req.full_url)
        return io.BytesIO(payload)
    route = f"/api/browser-voice/voices/{voice.VOICES_REVISION[:12]}/"
    with patch.object(voice.urllib.request, "urlopen", side_effect=fetch):
        for _ in range(2):
            handler = Handler(route + vid + ".safetensors")
            voice.handle(handler, "GET")
            assert handler.status == 200 and handler.wfile.getvalue() == payload
    assert calls == [voice.VOICES_BASE + vid + ".safetensors"], "second request is served from the cache"
    for bad in ["../secret.safetensors", "unknown.safetensors", vid]:
        handler = Handler(route + bad)
        voice.handle(handler, "GET")
        assert handler.status == 404


def test_corrupt_speaker_voice_is_rejected_and_not_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_BROWSER_VOICE_DIR", str(tmp_path))
    monkeypatch.setattr(voice, "_installed", lambda: True)
    vid = voice.VOICES[0][0]
    monkeypatch.setattr(voice, "_VOICE_INDEX", {vid: (vid, "Test", "US", "female", 4, hashlib.sha256(b"good").hexdigest())})

    class Handler:
        path = f"/api/browser-voice/voices/{voice.VOICES_REVISION[:12]}/{vid}.safetensors"
        def send_json(self, data, status=200):
            self.status = status
    handler = Handler()
    with patch.object(voice.urllib.request, "urlopen", return_value=io.BytesIO(b"evil")):
        voice.handle(handler, "GET")
    assert handler.status == 502
    assert not any(p.suffix == ".safetensors" for p in tmp_path.rglob("*"))
