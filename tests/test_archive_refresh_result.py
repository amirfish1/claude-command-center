"""The rebuild result reaches the dashboard in pieces small enough that decoding
it never holds the GIL for long (CCC-142)."""

import json

import pytest

import server
from ccc_server import archive_history_layer as layer


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


def test_delta_round_trips_changed_reordered_and_duplicate_rows(tmp_path):
    path = tmp_path / "out.json"
    result = _result(200)
    server._write_archive_refresh_result(path, result, basis=set())
    previous, full_size = server._read_archive_refresh_result(path)
    basis = previous.pop("transfer_rows")
    assert previous == result

    survivor = result["rows"][199]
    changed = dict(result["rows"][0], title="updated")
    result["rows"] = [survivor, changed, survivor, *result["rows"][2:199]]
    server._write_archive_refresh_result(path, result, basis=set(basis))
    with pytest.raises(ValueError, match="unknown row"):
        server._read_archive_refresh_result(path)
    decoded, delta_size = server._read_archive_refresh_result(path, basis=basis)
    assert len(decoded.pop("transfer_rows")) == 199
    assert decoded == result
    assert decoded["rows"][0] is previous["rows"][199]
    assert json.loads(path.read_text().splitlines()[0])["row_count"] == 1
    assert delta_size < full_size


def test_history_guard_reuses_only_hashes_for_published_transfer_objects(tmp_path, monkeypatch):
    previous = {"session_id": "old", "details": {"a": 1, "b": 2}}
    reordered = {"session_id": "old", "details": {"b": 2, "a": 1}}
    new = {"session_id": "new", "details": {"b": 2, "a": 1}}
    path = tmp_path / "out.json"
    server._write_archive_refresh_result(path, {"rows": [reordered, new]}, basis=set())
    decoded, _ = server._read_archive_refresh_result(path)
    assert list(decoded["rows"][1]) == ["details", "session_id"]
    assert list(decoded["rows"][1]["details"]) == ["b", "a"]

    expected = {"old": layer.hot_row_token(previous), "new": layer.hot_row_token(new)}
    assert expected["old"] != layer.hot_row_token(reordered)
    monkeypatch.setattr(server, "_archive_serve_cache", {})
    monkeypatch.setattr(server, "_archive_serve_version", 0)
    generation = server._archive_serve_generation
    assert server._archive_serve_cache_store("base", [previous], generation, hot_selected=True)
    assert server._archive_serve_cache_store(
        "base", decoded["rows"], generation, hot_selected=True, transfer_rows=decoded["transfer_rows"])

    computed = []
    original = layer.hot_row_token

    def count(row):
        computed.append(row["session_id"])
        return original(row)

    monkeypatch.setattr(layer, "hot_row_token", count)
    assert server._archive_history_hot_snapshot("base")[1] == expected
    assert computed == ["old"]
