"""The Grok variant-A scan runs inside every sessions-snapshot refresh, so
each session dir's summary.json + transcript head must be mined once per
file version, not once per poll."""
import os

from ccc_server import grok
from tests.test_grok_engine import _make_variant_a_home, SID_A


def _bump(path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


def test_grok_session_dirs_are_mined_once_per_file_version(monkeypatch, tmp_path):
    home = _make_variant_a_home(tmp_path)
    monkeypatch.setenv("GROK_HOME", str(home))
    grok._GROK_DIR_INFO_CACHE.clear()
    mined = []
    real = grok._grok_mine_jsonl_texts

    def counting(path):
        mined.append(str(path))
        return real(path)

    monkeypatch.setattr(grok, "_grok_mine_jsonl_texts", counting)

    first = grok._grok_sessions_from_dirs()
    assert [r["id"] for r in first] == [SID_A]
    assert len(mined) == 1

    second = grok._grok_sessions_from_dirs()
    assert len(mined) == 1
    assert second == first

    # Callers mutate the rows (parent links, listing fields): the memo must
    # hand out copies.
    second[0]["title"] = "mutated"
    assert grok._grok_sessions_from_dirs()[0]["title"] != "mutated"
    assert len(mined) == 1

    jsonl = sorted(home.rglob("*.jsonl"))[0]
    _bump(jsonl)
    grok._grok_sessions_from_dirs()
    assert len(mined) == 2

    key = str(jsonl.parent)
    assert key in grok._GROK_DIR_INFO_CACHE
    grok._GROK_DIR_INFO_CACHE.clear()
