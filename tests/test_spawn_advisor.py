import json
import stat

import pytest

from ccc_server import spawn_advisor as advisor


ENGINES = {"claude", "codex", "devin", "grok", "antigravity"}


def config():
    value = advisor.empty_config()
    value["enabled"] = True
    value["rankings"]["deep"] = [{"engine": "codex", "model": "example-model", "effort": "medium"}]
    value["resources"]["codex"] = {"remaining_pct": 75, "observed_at": "2026-01-01T12:00:00Z"}
    return value


def test_preferences_round_trip_and_private_permissions(tmp_path):
    assert advisor.load(tmp_path, ENGINES) == advisor.empty_config()
    saved = advisor.save(tmp_path, config(), ENGINES)
    assert advisor.load(tmp_path, ENGINES) == saved
    assert stat.S_IMODE((tmp_path / "spawn-advisor.json").stat().st_mode) == 0o600
    assert saved["rankings"]["deep"][0]["effort"] == "medium"


@pytest.mark.parametrize("field,value", [("remaining_pct", 101), ("remaining_pct", True),
                                           ("balance_usd", float("nan")), ("resets_at", "2026-01-01T12:00")])
def test_rejects_invalid_resource_values(tmp_path, field, value):
    data = config()
    data["resources"]["codex"][field] = value
    with pytest.raises(ValueError):
        advisor.save(tmp_path, data, ENGINES)
    assert not (tmp_path / "spawn-advisor.json").exists()


def test_rejects_unknown_engines_and_duplicate_choices():
    data = config()
    data["rankings"]["deep"][0]["engine"] = "unknown"
    with pytest.raises(ValueError):
        advisor.validate(data, ENGINES)
    data = config()
    data["rankings"]["deep"] *= 2
    with pytest.raises(ValueError):
        advisor.validate(data, ENGINES)


def test_corrupt_file_does_not_silently_replace_preferences(tmp_path):
    target = tmp_path / "spawn-advisor.json"
    target.write_text("broken", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        advisor.load(tmp_path, ENGINES)
    assert target.read_text() == "broken"


def test_cutoffs_windows_and_banked_resets_round_trip(tmp_path):
    data = config()
    data["cutoffs"] = {"deep": 5, "routine": None}
    data["resources"]["codex"].update(window_hours=24, plan_ends_at="2026-02-01T00:00:00Z",
                                      banked_resets=[{"label": "weekly", "count": 2,
                                                      "expires_at": "2026-01-10T00:00:00Z"}])
    saved = advisor.save(tmp_path, data, ENGINES)
    assert saved["cutoffs"] == {"deep": 1, "routine": None}  # clamped to the ranking length
    res = saved["resources"]["codex"]
    assert res["window_hours"] == 24 and res["banked_resets"][0]["count"] == 2
    assert res["plan_ends_at"].startswith("2026-02-01")
    assert advisor.load(tmp_path, ENGINES) == saved


@pytest.mark.parametrize("patch", [{"cutoffs": {"deep": -1}}, {"cutoffs": {"deep": True}},
                                   {"cutoffs": {"other": 1}}])
def test_rejects_invalid_cutoffs(patch):
    data = config()
    data.update(patch)
    with pytest.raises(ValueError):
        advisor.validate(data, ENGINES)


def test_rejects_invalid_banked_resets():
    data = config()
    data["resources"]["codex"]["banked_resets"] = [{"count": 0}]
    with pytest.raises(ValueError):
        advisor.validate(data, ENGINES)
