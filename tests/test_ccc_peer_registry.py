"""Tests for CCC's own Claude-peer registry row and key-file builders."""

import ccc_peer_uds as uds


def test_build_ccc_registry_row_matches_real_row_shape():
    row = uds.build_ccc_registry_row(
        4321, "/tmp/cc-socks/4321.sock", "/Users/x/Apps/claude-command-center",
        started_at_epoch_ms=1788026708705, proc_start="Sat Aug 29 18:05:03 2026",
    )
    assert row["pid"] == 4321
    assert row["messagingSocketPath"] == "/tmp/cc-socks/4321.sock"
    assert row["peerProtocol"] == 1
    assert row["name"] == "ccc"
    assert row["version"] == uds.CCC_PEER_COMPAT_VERSION
    assert row["cwd"] == "/Users/x/Apps/claude-command-center"
    assert row["startedAt"] == 1788026708705
    assert row["procStart"] == "Sat Aug 29 18:05:03 2026"
    assert isinstance(row["sessionId"], str) and row["sessionId"]
    assert row["peerFeatures"] == []  # CCC declares no optional peer features
    # Required-key set used by validate_registry_row_shape / resolve_target
    for key in ("pid", "sessionId", "cwd", "messagingSocketPath", "peerProtocol", "version"):
        assert key in row


def test_build_ccc_registry_row_session_id_is_stable_for_same_pid_and_socket():
    a = uds.build_ccc_registry_row(1, "/tmp/cc-socks/1.sock", "/x")
    b = uds.build_ccc_registry_row(1, "/tmp/cc-socks/1.sock", "/x")
    assert a["sessionId"] == b["sessionId"]  # deterministic, not random per call


def test_ccc_key_payload_shape():
    assert uds.ccc_key_payload("tok-abc") == {"peerToken": "tok-abc"}


REAL_ROW = {
    "pid": 21180, "sessionId": "2ed7cb45-5142-4ee4-912b-beb2d8d30748",
    "cwd": "/x", "startedAt": 1788026708705,
    "procStart": "Sat Aug 29 18:05:03 2026", "version": "2.1.251",
    "peerProtocol": 1, "peerFeatures": [], "kind": "interactive",
    "entrypoint": "sdk-cli", "pidDomain": "darwin",
    "messagingSocketPath": "/tmp/cc-socks/21180.sock", "name": "x",
    "nameSource": "user", "nameSince": 1788026708859, "updatedAt": 1788026708860,
}


def test_validate_registry_row_shape_passes_against_a_real_row():
    row = uds.build_ccc_registry_row(1, "/tmp/cc-socks/1.sock", "/x")
    result = uds.validate_registry_row_shape(row, [REAL_ROW])
    assert result == {"ok": True, "reason": ""}


def test_validate_registry_row_shape_flags_missing_required_key():
    row = uds.build_ccc_registry_row(1, "/tmp/cc-socks/1.sock", "/x")
    del row["peerProtocol"]
    result = uds.validate_registry_row_shape(row, [REAL_ROW])
    assert result["ok"] is False
    assert "peerProtocol" in result["reason"]


def test_validate_registry_row_shape_flags_drifted_reference_rows():
    row = uds.build_ccc_registry_row(1, "/tmp/cc-socks/1.sock", "/x")
    drifted = dict(REAL_ROW)
    del drifted["messagingSocketPath"]  # pretend Claude Code renamed the field
    result = uds.validate_registry_row_shape(row, [drifted])
    assert result["ok"] is False
    assert "no reference row" in result["reason"]


def test_validate_registry_row_shape_with_no_reference_rows_is_unverified_ok():
    # No live Claude session on this machine to cross-check against. Slice 3
    # must still work on a headless-only machine, so this is NOT a fail-closed
    # case -- proceed, but the caller (Task 3) logs that it was unverified.
    row = uds.build_ccc_registry_row(1, "/tmp/cc-socks/1.sock", "/x")
    result = uds.validate_registry_row_shape(row, [])
    assert result == {"ok": True, "reason": "no_reference_rows"}
