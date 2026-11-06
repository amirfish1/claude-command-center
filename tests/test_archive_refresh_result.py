"""The rebuild result reaches the dashboard in pieces small enough that decoding
it never holds the GIL for long (CCC-142)."""

import json

import pytest

import server


def _result(n):
    rows = [{"session_id": f"s{i}", "title": "é" * (i % 3)} for i in range(n)]
    return {"ok": True, "key": "k", "rows": rows, "from_cache": True, "phases": {"tier": "match"}}


@pytest.mark.parametrize("n", [0, 1, 63, 64, 65, 200])
def test_rows_round_trip_in_order(tmp_path, n):
    path = tmp_path / "out.json"
    result = _result(n)
    server._write_archive_refresh_result(path, result)
    decoded, size = server._read_archive_refresh_result(path)
    assert decoded == result
    assert size > 0


def test_each_line_stays_small(tmp_path):
    path = tmp_path / "out.json"
    server._write_archive_refresh_result(path, _result(1000))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 + -(-1000 // server._ARCHIVE_RESULT_ROWS_PER_LINE)
    assert all(len(json.loads(line)) <= server._ARCHIVE_RESULT_ROWS_PER_LINE for line in lines[1:])


def test_truncated_result_is_rejected(tmp_path):
    path = tmp_path / "out.json"
    server._write_archive_refresh_result(path, _result(200))
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("".join(lines[:-1]), encoding="utf-8")
    with pytest.raises(ValueError):
        server._read_archive_refresh_result(path)


def test_cut_off_line_is_rejected(tmp_path):
    path = tmp_path / "out.json"
    server._write_archive_refresh_result(path, _result(200))
    text = path.read_text(encoding="utf-8")
    path.write_text(text[:-20], encoding="utf-8")
    with pytest.raises(ValueError):
        server._read_archive_refresh_result(path)


def test_failed_worker_result_has_no_rows(tmp_path):
    path = tmp_path / "out.json"
    server._write_archive_refresh_result(path, {"ok": False, "error": "boom"})
    assert server._read_archive_refresh_result(path)[0] == {"ok": False, "error": "boom"}
