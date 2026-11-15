"""Playback-scoped CPU policy: restoration matters as much as boosting."""
import json
from pathlib import Path

import pytest

from ccc_server import tts_priority as tp

CLIENT = "a" * 32
OTHER = "b" * 32


@pytest.fixture
def trial(tmp_path):
    weight = tmp_path / "cpu.weight"
    weight.write_text("300")
    return tp.Trial(tmp_path / "state", weight, 1, now=1000)


def heartbeat(trial, playing=True, seq=1, now=1001, client=CLIENT):
    return tp.playback({"client": client, "playing": playing, "seq": seq}, trial.directory, now)


def test_disabled_by_default_and_no_lease_created(tmp_path):
    assert not tp.playback({"client": CLIENT, "playing": True, "seq": 1}, tmp_path, 1000)["enabled"]
    assert not list(tmp_path.glob("lease-*"))


def test_play_pause_resume_stop_restores_exact_baseline(trial):
    assert trial.tick(1000)
    assert trial.weight_file.read_text() == "300"
    heartbeat(trial)
    trial.tick(1001)
    assert trial.weight_file.read_text() == "30"
    heartbeat(trial, False, seq=2, now=1002)
    trial.tick(1002)
    assert trial.weight_file.read_text() == "300"
    heartbeat(trial, seq=3, now=1003)
    trial.tick(1003)
    assert trial.weight_file.read_text() == "30"
    heartbeat(trial, False, seq=4, now=1004)
    trial.tick(1004)
    assert trial.weight_file.read_text() == "300"


def test_late_heartbeat_cannot_override_newer_stop(trial):
    heartbeat(trial, False, seq=2)
    heartbeat(trial, True, seq=1)
    trial.tick(1001)
    assert trial.weight_file.read_text() == "300"


def test_delayed_heartbeat_cannot_revive_an_expired_stop(trial):
    heartbeat(trial, False, seq=2)
    trial.tick(1020)
    heartbeat(trial, True, seq=1, now=1021)
    trial.tick(1021)
    assert trial.weight_file.read_text() == "300"


def test_other_tab_stop_does_not_cancel_active_tab(trial):
    heartbeat(trial)
    heartbeat(trial, False, client=OTHER)
    trial.tick(1001)
    assert trial.weight_file.read_text() == "30"
    trial.tick(1001 + tp.LEASE_SECONDS + 1)
    assert trial.weight_file.read_text() == "300"


def test_trial_expires_despite_fresh_heartbeat(trial):
    heartbeat(trial)
    trial.tick(1001)
    heartbeat(trial, seq=2, now=1059)
    assert not trial.tick(1060)
    assert trial.weight_file.read_text() == "300"


def test_disable_releases_without_waiting_for_lease(trial):
    heartbeat(trial)
    trial.tick(1001)
    (trial.directory / "disabled").touch()
    assert not trial.tick(1002)
    assert trial.weight_file.read_text() == "300"
    assert not heartbeat(trial, seq=2, now=1003)["enabled"]


def test_external_change_is_preserved_even_by_recovery(trial):
    heartbeat(trial)
    trial.tick(1001)
    trial.weight_file.write_text("200")
    assert not trial.tick(1002)
    tp.restore(trial.directory, trial.weight_file)
    assert trial.weight_file.read_text() == "200"
    assert "externally" in tp.status(trial.directory, 1002)["error"]


def test_external_change_to_trial_weight_while_idle_is_not_ours(trial):
    trial.weight_file.write_text("30")
    assert not trial.tick(1001)
    tp.restore(trial.directory, trial.weight_file)
    assert trial.weight_file.read_text() == "30"


def test_crash_recovery_before_and_after_cgroup_write(trial):
    # Persist-before-write state survives a process killed at either side.
    state = dict(trial.state, boosted=True, restore_pending=True)
    (trial.directory / "trial.json").write_text(json.dumps(state))
    tp.restore(trial.directory, trial.weight_file)
    assert trial.weight_file.read_text() == "300"
    (trial.directory / "trial.json").write_text(json.dumps(state))
    trial.weight_file.write_text("30")
    tp.restore(trial.directory, trial.weight_file)
    assert trial.weight_file.read_text() == "300"
    assert not tp.status(trial.directory, 1001)["enabled"]


def test_bounded_duration_and_never_increases_existing_lower_weight(tmp_path):
    weight = tmp_path / "cpu.weight"
    weight.write_text("10")
    trial = tp.Trial(tmp_path / "state", weight, 10000, now=1000)
    assert trial.state["until"] == 1000 + tp.MAX_TRIAL_SECONDS
    heartbeat(trial)
    trial.tick(1001)
    assert weight.read_text() == "10"


@pytest.mark.parametrize("payload", [
    {"client": "../escape", "playing": True, "seq": 1},
    {"client": CLIENT, "playing": "true", "seq": 1},
    {"client": CLIENT, "playing": True, "seq": True},
    {"client": CLIENT, "playing": True, "seq": -1},
])
def test_invalid_leases_rejected(tmp_path, payload):
    with pytest.raises(ValueError):
        tp.playback(payload, tmp_path, 1000)
