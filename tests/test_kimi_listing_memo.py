"""find_kimi_conversations runs inside every sessions-snapshot refresh and
read state.json plus the wire.jsonl head for every indexed session each
time (~700 file reads per refresh). Read once per file version."""
import json
import os

from ccc_server import kimi_store


def _bump(path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


def _make_session(tmp_path):
    sdir = tmp_path / "kimi-session"
    (sdir / "agents" / "main").mkdir(parents=True)
    (sdir / "state.json").write_text(json.dumps({"cwd": "/work/fake", "title": "Fake"}))
    (sdir / "agents" / "main" / "wire.jsonl").write_text("\n".join([
        json.dumps({"type": "config.update", "modelAlias": "k2"}),
        json.dumps({"type": "turn.prompt", "input": [{"type": "text", "text": "fix the fake thing"}]}),
    ]) + "\n")
    return sdir


def test_state_meta_and_wire_head_are_read_once_per_file_version(monkeypatch, tmp_path):
    sdir = _make_session(tmp_path)
    kimi_store._KIMI_LISTING_FILE_CACHE.clear()
    opened = []
    real_open = kimi_store.Path.open

    def counting_open(self, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if "w" not in mode and "a" not in mode:  # write_text also goes through open()
            opened.append(self.name)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(kimi_store.Path, "open", counting_open)

    meta = kimi_store._kimi_state_meta(str(sdir))
    wire = kimi_store._kimi_wire_head(str(sdir))
    assert meta["title"] == "Fake"
    assert wire["first_prompt"] == "fix the fake thing"
    assert wire["model"] == "k2"
    assert sorted(opened) == ["state.json", "wire.jsonl"]

    meta2 = kimi_store._kimi_state_meta(str(sdir))
    wire2 = kimi_store._kimi_wire_head(str(sdir))
    assert len(opened) == 2
    assert meta2 == meta and wire2 == wire
    # Callers mutate rows: the memo must hand out copies.
    meta2["title"] = "mutated"
    assert kimi_store._kimi_state_meta(str(sdir))["title"] == "Fake"

    # Same-size rewrite: only the mtime moves, and that must be enough.
    (sdir / "state.json").write_text(json.dumps({"cwd": "/work/fake", "title": "Faky"}))
    _bump(sdir / "state.json")
    assert kimi_store._kimi_state_meta(str(sdir))["title"] == "Faky"
    assert len(opened) == 3
    kimi_store._KIMI_LISTING_FILE_CACHE.clear()


def test_missing_session_dir_is_cheap_and_not_cached_forever(tmp_path):
    kimi_store._KIMI_LISTING_FILE_CACHE.clear()
    assert kimi_store._kimi_state_meta(str(tmp_path / "nope")) == {}
    assert kimi_store._kimi_wire_head(str(tmp_path / "nope"))["first_prompt"] == ""
    assert kimi_store._kimi_state_meta("") == {}
    kimi_store._KIMI_LISTING_FILE_CACHE.clear()
