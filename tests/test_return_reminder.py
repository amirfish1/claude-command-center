"""The one opt-in reminder: fires once, says something true, never repeats."""

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from ccc_server import notify, popups, return_reminder


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(return_reminder, "STATE_PATH", tmp_path / "return-reminder.json")
    monkeypatch.setattr(return_reminder, "_desktop_notify", lambda title, body: True)
    monkeypatch.setattr(notify, "LOG_FILE", tmp_path / "notify-log.json")
    monkeypatch.setattr(popups, "APPROVED", frozenset())


NOON = datetime(2026, 11, 5, 12, 0).timestamp()
NEXT_NINE = datetime(2026, 11, 6, 9, 0).timestamp()


def _transcript(root, project, name, rows, mtime):
    folder = root / project
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    os.utime(path, (mtime, mtime))
    return path


def _assistant(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _user(text):
    return {"type": "user", "message": {"content": text}}


def _fire(tmp_path, now=NEXT_NINE + 60):
    sent = []
    item = return_reminder.tick(
        now=now, base_url="http://127.0.0.1:8123", publish=sent.append,
        projects_dir=tmp_path / "projects")
    return item, sent


def test_nothing_is_armed_until_the_user_asks(tmp_path):
    assert return_reminder.status()["armed"] is False
    assert _fire(tmp_path) == (None, [])


def test_arm_targets_nine_the_next_morning():
    assert return_reminder.arm(now=NOON)["due_at"] == NEXT_NINE


def test_arming_late_at_night_still_means_the_next_calendar_morning():
    late = datetime(2026, 11, 5, 23, 59).timestamp()
    assert return_reminder.arm(now=late)["due_at"] == NEXT_NINE


def test_waits_until_due(tmp_path):
    return_reminder.arm(now=NOON)
    assert _fire(tmp_path, now=NEXT_NINE - 60) == (None, [])
    assert return_reminder.status()["armed"] is True


def test_fires_once_with_a_true_line_and_ignores_the_popup_gate(tmp_path):
    root = tmp_path / "projects"
    _transcript(root, "-a", "s1", [_user("do it"), _assistant("Done. Card or credit?")], NOON + 60)
    _transcript(root, "-a", "s2", [_user("x"), _assistant("All done.")], NOON + 120)
    _transcript(root, "-b", "s3", [_assistant("Ready?"), _user("yes")], NOON + 180)
    _transcript(root, "-c", "old", [_assistant("Before arming?")], NOON - 600)
    return_reminder.arm(now=NOON)

    item, sent = _fire(tmp_path)

    assert item["body"] == ("Since yesterday: 3 Claude sessions in 2 projects. "
                            "1 ended on a question for you. Your board: http://localhost:8123")
    assert item["delivered"] is True and sent and notify.history()
    assert _fire(tmp_path, now=NEXT_NINE + 120) == (None, [])
    assert return_reminder.status()["armed"] is False


def test_an_idle_day_says_so(tmp_path):
    return_reminder.arm(now=NOON)
    item, _ = _fire(tmp_path)
    assert item["body"] == "No Claude sessions since yesterday. Your board: http://localhost:8123"


def test_singular_wording():
    assert return_reminder.compose({"sessions": 1, "projects": 1}).startswith(
        "Since yesterday: 1 Claude session in 1 project.")


def test_tool_use_as_last_block_is_not_a_question(tmp_path):
    row = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Shall I?"}, {"type": "tool_use", "name": "Bash"}]}}
    path = _transcript(tmp_path, "-a", "s", [row], NOON)
    assert return_reminder._last_turn_asks(path) is False


def test_a_reminder_days_late_is_dropped(tmp_path):
    return_reminder.arm(now=NOON)
    assert _fire(tmp_path, now=NEXT_NINE + 4 * 86400) == (None, [])
    assert return_reminder.status()["armed"] is False


def test_cancel_and_corrupt_state_leave_nothing_armed(tmp_path):
    return_reminder.arm(now=NOON)
    assert return_reminder.cancel()["armed"] is False
    return_reminder.STATE_PATH.write_text("not json")
    assert _fire(tmp_path) == (None, [])
    assert not return_reminder.STATE_PATH.exists()


def test_a_failed_tick_does_not_refire(tmp_path, monkeypatch):
    return_reminder.arm(now=NOON)
    monkeypatch.setattr(return_reminder, "summarize", lambda *a: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        _fire(tmp_path)
    assert return_reminder.status()["armed"] is False


def test_service_detection(monkeypatch):
    monkeypatch.setenv("XPC_SERVICE_NAME", "0")
    monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert return_reminder.runs_in_background() is False
    monkeypatch.setenv("XPC_SERVICE_NAME", "com.github.claude-command-center.server")
    assert return_reminder.runs_in_background() is True


def test_ccc_start_is_the_same_as_bare_ccc():
    source = (Path(__file__).resolve().parent.parent / "ccc").read_text(encoding="utf-8")
    start = source.index('if argv[:1] == ["start"]')
    assert start < source.index("if not argv or argv[0] in RUN_SH_FLAGS")
