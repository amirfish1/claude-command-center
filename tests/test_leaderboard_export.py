import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "export-leaderboard.py"
SPEC = importlib.util.spec_from_file_location("leaderboard_export", SCRIPT)
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)
STAMP = "2026-10-06T12:00:00+00:00"


def model(model_id="example/model-a", **patch):
    return {
        "id": model_id,
        "score": 84.0,
        "pass_rate": 0.8,
        "passed": 4,
        "tasks": 5,
        "median_request_ms": 0,
        "supports_tools": True,
        "evaluated_at": STAMP,
        **patch,
    }


def store(*rows):
    return {"version": 1, "tasks": list(exporter.TASK_TITLES),
            "models": {str(index): row for index, row in enumerate(rows)}}


def cli(*args, env=None):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, env=env, timeout=10)


def test_export_is_an_exact_public_whitelist():
    entry = model(name="PRIVATE_SENTINEL", platform="PRIVATE_SENTINEL",
                  error="PRIVATE_SENTINEL", prompt="PRIVATE_SENTINEL",
                  per_task=[{"title": "PRIVATE_SENTINEL", "detail": "PRIVATE_SENTINEL"}])
    source = {**store(entry), "router": "PRIVATE_SENTINEL", "updated_at": "PRIVATE_SENTINEL",
              "best": {"model": "PRIVATE_SENTINEL", "reason": "PRIVATE_SENTINEL"},
              "api_key": "sk-ant-test-XXXX", "owner": "PRIVATE_SENTINEL"}
    previous = copy.deepcopy(source)
    payload = exporter.build_payload(source, STAMP)
    assert payload == {
        "version": 1, "generated_at": STAMP, "last_eval_at": STAMP,
        "tasks": [{"id": key, "title": value} for key, value in exporter.TASK_TITLES.items()],
        "best": "example/model-a", "models": [{**model(), "rank": 1}],
    }
    assert "PRIVATE_SENTINEL" not in json.dumps(payload)
    assert "sk-ant-test-XXXX" not in json.dumps(payload)
    assert source == previous
    exporter.validate_payload(payload)


def test_ranking_preserves_saved_scores_zero_median_and_missing_median():
    payload = exporter.build_payload(store(
        model("example/slow", median_request_ms=2100),
        model("example/zero-b"), model("example/missing", median_request_ms=None),
        model("example/zero-a"), model("example/higher", score=90.3),
    ), STAMP)
    assert [m["id"] for m in payload["models"]] == [
        "example/higher", "example/zero-a", "example/zero-b", "example/slow", "example/missing",
    ]
    assert [m["rank"] for m in payload["models"]] == [1, 2, 3, 4, 5]
    assert payload["models"][0]["score"] == 90.3
    assert payload["models"][1]["median_request_ms"] == 0
    assert payload["models"][-1]["median_request_ms"] is None


def test_best_is_top_tool_ready_result_not_the_private_pinned_preference():
    source = store(model("example/text", score=100, supports_tools=False),
                   model("example/tool"), model("example/preference", score=20))
    source["best"] = {"model": "example/preference", "reason": "PRIVATE_SENTINEL"}
    payload = exporter.build_payload(source, STAMP)
    assert payload["best"] == "example/tool"
    assert payload["models"][0]["id"] == "example/text"
    assert exporter.build_payload(store(model(passed=0, pass_rate=0)), STAMP)["best"] is None


def test_dates_are_normalized_and_last_race_uses_actual_time_not_string_order():
    payload = exporter.build_payload(store(
        model("example/later-clock", evaluated_at="2026-10-06T13:00:00+02:00"),
        model("example/later-time", evaluated_at="2026-10-06T11:30:00Z"),
    ), STAMP)
    assert payload["last_eval_at"] == "2026-10-06T11:30:00+00:00"
    assert payload["models"][0]["evaluated_at"] == "2026-10-06T11:00:00+00:00"


def test_duplicate_model_ids_keep_latest_complete_result():
    payload = exporter.build_payload(store(
        model(score=10, evaluated_at="2026-10-05T00:00:00Z"), model(),
    ), STAMP)
    assert payload["models"] == [{**model(), "rank": 1}]


@pytest.mark.parametrize("patch", [
    {"id": ""}, {"id": "person@example.invalid"}, {"id": "/home/example/private"},
    {"id": "home/example/private"}, {"id": "https://example.invalid/model"},
    {"id": "sk-ant-test-XXXX"}, {"id": "Bearer sk-ant-test-XXXX"},
    {"id": "<img src=x onerror=alert(1)>"}, {"id": "../private"},
    {"id": "C:/private"}, {"id": "example/line\n"}, {"id": "x" * 161},
    {"score": float("nan")}, {"score": float("inf")}, {"score": True},
    {"score": -1}, {"score": 101}, {"score": "84"},
    {"pass_rate": float("nan")}, {"pass_rate": True}, {"pass_rate": -0.1},
    {"pass_rate": 1.1}, {"pass_rate": 0.2}, {"passed": True}, {"passed": 6},
    {"passed": -1}, {"passed": 4.5}, {"tasks": 0}, {"tasks": "5"},
    {"median_request_ms": -1}, {"median_request_ms": float("inf")},
    {"median_request_ms": True}, {"median_request_ms": "100"},
    {"supports_tools": "false"}, {"evaluated_at": None},
    {"evaluated_at": "PRIVATE_SENTINEL"}, {"evaluated_at": "2026-10-06"},
])
def test_invalid_or_incomplete_rows_are_excluded_without_fabricated_zeros(patch):
    payload = exporter.build_payload(store(model(**patch)), STAMP)
    assert payload["models"] == []
    assert payload["last_eval_at"] is None
    assert payload["best"] is None
    json.dumps(payload, allow_nan=False)


@pytest.mark.parametrize("model_id", ["qwen/qwen3-coder:free", "deepseek-v3.2", "provider/model+tools"])
def test_public_catalog_style_ids_are_supported(model_id):
    assert exporter.build_payload(store(model(model_id)), STAMP)["models"][0]["id"] == model_id


@pytest.mark.parametrize("source", [[], {"models": []}, {"models": None}, {"version": 2},
                                     {"version": True}, {"tasks": ["unknown"]},
                                     {"tasks": [[], "fix_test", "add_function", "tool_read_write", "multi_step"]}])
def test_bad_store_schema_is_rejected(source):
    with pytest.raises(ValueError):
        exporter.build_payload(source, STAMP)


def test_matches_existing_eval_aggregates_without_starting_a_race():
    from ccc_server import free_eval
    per_task = [{"passed": i < 4, "median_request_ms": 2000} for i in range(5)]
    measured = free_eval._score_model(per_task)
    source = store(model(**measured))
    payload = exporter.build_payload(source, STAMP)
    row = payload["models"][0]
    assert measured == {"score": 83.5, "passed": 4, "tasks": 5, "pass_rate": 0.8, "median_request_ms": 2000}
    assert {key: row[key] for key in measured} == measured


def test_missing_store_and_empty_store_are_honest_empty_results(tmp_path):
    target = tmp_path / "public" / "data.json"
    result = cli("--store", tmp_path / "missing.json", "--out", target)
    assert result.returncode == 0
    payload = json.loads(target.read_text())
    assert payload["models"] == [] and payload["best"] is None and payload["last_eval_at"] is None
    assert len(payload["tasks"]) == 5
    assert "No scores published" in result.stdout
    assert not list(target.parent.glob(".leaderboard-*"))
    assert cli("--validate", target).returncode == 0


@pytest.mark.parametrize("content", ["{bad json PRIVATE_SENTINEL", "[]", '{"models": []}', '{"version": 9}'])
def test_corrupt_store_never_overwrites_previous_export(tmp_path, content):
    source = tmp_path / "private.json"
    target = tmp_path / "data.json"
    source.write_text(content)
    target.write_text("PREVIOUS_EXPORT")
    result = cli("--store", source, "--out", target)
    assert result.returncode != 0
    assert target.read_text() == "PREVIOUS_EXPORT"
    assert "PRIVATE_SENTINEL" not in result.stderr
    assert "Traceback" not in result.stderr


def test_check_is_read_only_and_env_override_works(tmp_path):
    source = tmp_path / "private eval.json"
    target = tmp_path / "not-created" / "data.json"
    source.write_text(json.dumps(store(model())))
    before = source.read_bytes()
    result = cli("--check", "--out", target, env={**os.environ, "CCC_FREE_EVAL_STATE": str(source)})
    assert result.returncode == 0
    assert json.loads(result.stdout)["models"][0]["id"] == "example/model-a"
    assert not target.parent.exists()
    assert source.read_bytes() == before


def test_source_store_cannot_be_overwritten(tmp_path):
    source = tmp_path / "private.json"
    source.write_text(json.dumps(store(model())))
    before = source.read_bytes()
    result = cli("--store", source, "--out", source)
    assert result.returncode == 1
    assert source.read_bytes() == before


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(router="PRIVATE_SENTINEL"),
    lambda p: p["models"][0].update(name="PRIVATE_SENTINEL"),
    lambda p: p["tasks"][0].update(title="PRIVATE_SENTINEL"),
    lambda p: p["models"][0].update(rank=2),
    lambda p: p["models"][0].update(rank=True),
    lambda p: p.update(best="PRIVATE_SENTINEL"),
    lambda p: p.update(generated_at="PRIVATE_SENTINEL"),
    lambda p: p.update(last_eval_at="2026-10-05T00:00:00+00:00"),
    lambda p: p.update(models=[p["models"][0], p["models"][0]]),
])
def test_publish_validation_refuses_private_fields_and_invalid_snapshot(mutate):
    payload = exporter.build_payload(store(model()), STAMP)
    mutate(payload)
    with pytest.raises(ValueError):
        exporter.validate_payload(payload)


def test_committed_seed_has_no_invented_race_or_scores():
    payload = json.loads((ROOT / "site" / "leaderboard" / "data.json").read_text())
    assert payload["generated_at"] is None
    assert payload["last_eval_at"] is None
    assert payload["models"] == [] and payload["best"] is None
    exporter.validate_payload(payload)
