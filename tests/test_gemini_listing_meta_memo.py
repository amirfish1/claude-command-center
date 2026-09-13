"""find_gemini_conversations runs inside every sessions-snapshot refresh and
re-read and JSON-decoded every chat file on disk each time just to learn its
sessionId and lastUpdated. Decode once per file version."""
import json
import os

import server  # noqa: F401  (registers the core module gemini binds to)
from ccc_server import gemini


def _bump(path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


def test_listing_meta_decodes_each_chat_once_per_file_version(monkeypatch, tmp_path):
    chat = tmp_path / "session-abc.json"
    chat.write_text(json.dumps({"sessionId": "abc", "lastUpdated": "2026-07-20T08:00:00Z", "messages": []}))
    gemini._GEMINI_LISTING_META_CACHE.clear()
    loads = []
    real = gemini._load_gemini_chat

    def counting(path):
        loads.append(str(path))
        return real(path)

    monkeypatch.setattr(gemini, "_load_gemini_chat", counting)

    meta = gemini._gemini_chat_listing_meta(chat)
    assert meta == {"sessionId": "abc", "lastUpdated": "2026-07-20T08:00:00Z"}
    again = gemini._gemini_chat_listing_meta(chat)
    assert again == meta and len(loads) == 1
    again["sessionId"] = "mutated"
    assert gemini._gemini_chat_listing_meta(chat)["sessionId"] == "abc"

    chat.write_text(json.dumps({"sessionId": "abc", "lastUpdated": "2026-07-20T09:00:00Z", "messages": []}))
    _bump(chat)
    assert gemini._gemini_chat_listing_meta(chat)["lastUpdated"] == "2026-07-20T09:00:00Z"
    assert len(loads) == 2

    assert gemini._gemini_chat_listing_meta(tmp_path / "missing.json") is None
    gemini._GEMINI_LISTING_META_CACHE.clear()
