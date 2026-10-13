"""Queue ticket placement (mac / cloud / any): the ticket panel can edit it."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_ux_fixes_update_allows_placement():
    src = (ROOT / "server.py").read_text()
    m = re.search(r'editable = \(([^)]*)\)', src[src.index('"/api/ux-fixes/update"'):])
    assert m and '"placement"' in m.group(1)


def test_ticket_panel_has_runs_on_setting():
    js = (ROOT / "static" / "app.js").read_text()
    assert "_propSel('Runs on', 'placement'" in js
    for v in ("'mac'", "'cloud'", "'any'"):
        assert v in js.split("_propSel('Runs on'", 1)[1][:200]


def test_watchtower_roundtrip_when_available(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setenv("WATCHTOWER_STORE", str(tmp_path / "q.json"))
    monkeypatch.setenv("WATCHTOWER_ACTIVITY_LOG", str(tmp_path / "a.log"))
    q = pytest.importorskip("watchtower.queue")
    if not hasattr(q, "VALID_PLACEMENTS"):
        pytest.skip("installed WatchTower predates placement")
    it = q.enqueue(project="PLC", note="gui check")
    assert q.update(it["ref"], placement="mac")["placement"] == "mac"
