import importlib
import pathlib
import sys
import tempfile


def test_manual_usage_reset_events_can_be_updated_and_deleted():
    sys.modules.pop("server", None)
    server = importlib.import_module("server")

    with tempfile.TemporaryDirectory() as tmp:
        usage_dir = pathlib.Path(tmp) / "usage"
        old_events = server._RESET_EVENTS_FILE
        old_override = server._WEEK_START_OVERRIDE_FILE
        try:
            server._RESET_EVENTS_FILE = usage_dir / "reset-events.jsonl"
            server._WEEK_START_OVERRIDE_FILE = usage_dir / "week-start-override.json"

            created = server.record_usage_reset_event(
                "seven_day",
                reset_at="2026-07-02T17:05:00Z",
                source="user",
            )
            assert created["ok"]
            event_id = created["event"]["id"]

            updated = server.update_usage_reset_event(
                event_id,
                reset_at="2026-07-02T18:15:00Z",
            )
            assert updated["ok"]
            assert updated["event"]["id"] == event_id
            assert updated["event"]["detected_at"] == "2026-07-02T18:15:00Z"

            payload = server.usage_reset_events_payload(days=2, now_epoch=1_783_020_000)
            assert [event["id"] for event in payload["events"]] == [event_id]
            assert payload["events"][0]["detected_at"] == "2026-07-02T18:15:00Z"

            deleted = server.delete_usage_reset_event(event_id)
            assert deleted["ok"]
            payload = server.usage_reset_events_payload(days=2, now_epoch=1_783_020_000)
            assert payload["events"] == []
        finally:
            server._RESET_EVENTS_FILE = old_events
            server._WEEK_START_OVERRIDE_FILE = old_override
