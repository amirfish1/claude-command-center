"""CCC-1188: a Devin /compact that can't be delivered right now is queued.

`_queue_devin_steer` returns a bool; the compact path used to call
`.setdefault` on it, which raised AttributeError and 500'd the request.
"""

import server  # noqa: F401  (binds ccc_server.core)
from ccc_server import compact
from ccc_server import core as _core


def test_busy_devin_compact_is_queued_not_crashed(monkeypatch):
    queued = []
    monkeypatch.setattr(_core, "_detect_session_engine", lambda sid: "devin")
    monkeypatch.setattr(_core, "_is_devin_cli_session", lambda sid: True)
    monkeypatch.setattr(_core, "_devin_cli_raw_id", lambda sid: "raw-" + sid)
    monkeypatch.setattr(_core, "find_session_cwd", lambda sid: "")
    monkeypatch.setattr(_core, "_acp_prompt",
                        lambda h, sid, text, **kw: {"ok": False, "code": "busy"})
    monkeypatch.setattr(_core, "_queue_devin_steer",
                        lambda sid, text: queued.append((sid, text)) or True)

    result = compact._compact_session_context_impl("devin-cli-abc")

    assert queued == [("devin-cli-abc", "/compact")]
    assert result["ok"] is True
    assert result["queued"] is True
    assert result["compact"] is True
    assert result["engine"] == "devin"
