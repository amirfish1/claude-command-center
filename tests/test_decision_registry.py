"""Passive registry contracts, using isolated databases and generic fixtures."""

import copy
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import uuid

import pytest

from ccc_server.decision_registry import DecisionRegistry


@pytest.fixture
def registry(tmp_path):
    return DecisionRegistry(tmp_path / "decision-inbox" / "requests.sqlite3")


def source(**changes):
    record = {"adapter": "example", "origin_id": "thread-1", "generation": 1,
              "title": "Choose a color", "aliases": ["Q7"],
              "provenance": {"origin": "example", "observed_at": "2026-01-01"}}
    record.update(changes)
    return record


def test_import_is_idempotent_across_restart_and_preserves_legacy(registry):
    cards = {"dc_example": {"id": "dc_example", "decision_number": 12,
             "aliases": ["Q007", "D0012", "[Q7:color]"], "status": "snoozed",
             "title": "Choose a color", "source_id": "example:thread-1",
             "source": {"origin": "example"}, "options": [{"label": "Blue"}]}}
    original = copy.deepcopy(cards)
    first = registry.import_cards(cards)[0]
    reopened = DecisionRegistry(registry.db_path)
    assert reopened.installation_id == registry.installation_id
    assert str(uuid.UUID(first["request_id"])) == first["request_id"]
    assert reopened.import_cards(cards) == [first]
    assert reopened.list_requests() == [first]
    assert first["revision"] == 1
    assert first["status"] == "snoozed"
    assert first["content"] == original["dc_example"]
    assert set(first["aliases"]) == {"dc_example", "D12", "Q007", "D0012", "[Q7:color]"}
    assert cards == original


def test_revision_tracks_edits_but_identity_tracks_generation(registry):
    initial = registry.upsert_requests([source()])[0]
    assert registry.upsert_requests([source()])[0] == initial
    edited = registry.upsert_requests([source(title="Choose a size")])[0]
    assert edited["request_id"] == initial["request_id"]
    assert edited["revision"] == 2
    assert registry.upsert_requests([source(title="Choose a size")])[0] == edited
    regenerated = registry.upsert_requests([source(generation=2)])[0]
    assert regenerated["request_id"] != initial["request_id"]
    assert regenerated["content"]["title"] == initial["content"]["title"]
    assert regenerated["revision"] == 1
    assert regenerated["answer_state"] == regenerated["delivery_state"] == "none"


def test_q_alias_never_merges_origins_adapters_or_legacy_cards(registry):
    rows = registry.upsert_requests([
        source(), source(origin_id="thread-2"), source(adapter="other")])
    rows += registry.import_cards([
        {"id": "dc_one", "aliases": ["Q7"], "source_id": "same"},
        {"id": "dc_two", "aliases": ["Q7"], "source_id": "same"}])
    assert len({row["request_id"] for row in rows}) == 5
    assert all("Q7" in row["aliases"] for row in rows)
    assert len(registry.list_requests()) == 5


def test_installation_identity_lives_in_database_and_is_independent(registry, tmp_path):
    other = DecisionRegistry(tmp_path / "other.sqlite3")
    assert registry.installation_id != other.installation_id
    with sqlite3.connect(registry.db_path) as db:
        assert db.execute("SELECT value FROM metadata WHERE key='installation_id'").fetchone()[0] == registry.installation_id
    one = registry.upsert_requests([source()])[0]
    two = other.upsert_requests([source()])[0]
    assert one["request_id"] != two["request_id"]


@pytest.mark.parametrize("result", [{"ok": True}, {"ok": False}, None])
def test_legacy_answer_does_not_claim_delivery_or_replay(registry, result):
    card = {"id": "dc_answered", "status": "decided",
            "decided": {"option": 0, "result": result},
            "options": [{"action": {"kind": "inject", "prompt": "Example"}}]}
    row = registry.import_cards([card])[0]
    assert row["answer_state"] == "recorded"
    assert row["delivery_state"] == "unknown"
    assert row["content"] == card
    assert registry.import_cards([card])[0] == row


def test_empty_snapshots_never_withdraw_requests(registry):
    row = registry.upsert_requests([source()])[0]
    assert registry.upsert_requests([], complete=False) == []
    assert registry.upsert_requests([], complete=True) == []
    assert registry.import_cards({}) == []
    assert registry.list_requests() == [row]


def test_inputs_and_returned_records_are_detached(registry):
    request = source()
    before = copy.deepcopy(request)
    row = registry.upsert_requests([request])[0]
    assert request == before
    request["provenance"]["origin"] = "changed"
    row["content"]["provenance"]["origin"] = "also changed"
    row["aliases"].append("Q8")
    assert registry.list_requests()[0]["content"] == before
    assert registry.list_requests()[0]["aliases"] == ["Q7"]


def test_alias_edits_keep_history_without_merging(registry):
    first = registry.upsert_requests([source()])[0]
    second = registry.upsert_requests([source(aliases=["Q8"])])[0]
    assert second["request_id"] == first["request_id"]
    assert second["revision"] == 2
    assert second["aliases"] == ["Q7", "Q8"]


def test_batches_roll_back_on_invalid_identity(registry):
    with pytest.raises(ValueError, match="generation"):
        registry.upsert_requests([source(), source(origin_id="thread-2", generation=None)])
    assert registry.list_requests() == []
    with pytest.raises(ValueError, match="legacy card id"):
        registry.import_cards([{"id": "dc_ok"}, {"title": "No identity"}])
    assert registry.list_requests() == []


def test_concurrent_initialization_and_upsert_are_idempotent(tmp_path):
    db_path = tmp_path / "decision-inbox" / "requests.sqlite3"

    def write(_):
        instance = DecisionRegistry(db_path)
        return instance.upsert_requests([source()])[0]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(24)))
    assert all(row == results[0] for row in results)
    assert results[0]["revision"] == 1
    assert DecisionRegistry(db_path).list_requests() == [results[0]]


def test_concurrent_distinct_edits_increment_revision_without_lost_updates(registry):
    original = registry.upsert_requests([source()])[0]
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda n: registry.upsert_requests([
            source(title=f"Choice {n}", aliases=[f"Q{100+n}"])
        ])[0], range(16)))
    final = registry.list_requests()[0]
    assert final["revision"] == 17
    assert {row["revision"] for row in rows} == set(range(2, 18))
    assert all(row["request_id"] == original["request_id"] for row in rows)
    assert set(final["aliases"]) == {"Q7"} | {f"Q{100+n}" for n in range(16)}
