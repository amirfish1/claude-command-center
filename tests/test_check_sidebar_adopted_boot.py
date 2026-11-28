"""scripts/check-sidebar-adopted-boot.py joins boot reads to their server rows (CCC-730)."""

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("check_sidebar_adopted_boot",
                                               SCRIPTS / "check-sidebar-adopted-boot.py")
check_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_mod)

T0 = datetime(2026, 11, 28, 0, 0, tzinfo=timezone.utc)


def _client(seconds, server_ms, *, ms=600, automated=False, boot=True):
    ts = T0 + timedelta(seconds=seconds)
    row = {"kind": "list_read", "ts": ts.isoformat(), "_ts": ts, "ms": ms, "build": "b",
           "boot_id": f"b{seconds}", "up_s": 7200, "load1": 1.0, "ncpu": 16,
           "detail": {"server_ms": server_ms, "status": 200}}
    if boot:
        row["detail"]["boot"] = {"route": "same-origin"}
    if automated:
        row["automated"] = True
    return row


def _server(seconds, ms, since, status="delta", **extra):
    ts = T0 + timedelta(seconds=seconds)
    return {"kind": "list_server", "ts": ts.isoformat(), "_ts": ts, "ms": ms,
            "detail": dict({"since": since, "status": status, "window": "1d", "reader": True}, **extra)}


class AdoptedBootCheckTest(unittest.TestCase):
    def test_joins_token_boot_reads_and_skips_others(self):
        clients = [
            _client(10, 700),                     # adopted, slow
            _client(20, 150),                     # ok delta, fast
            _client(30, 900),                     # full body (since=none): not this gate
            _client(40, 800, automated=True),     # probe
            _client(50, 820, boot=False),         # a later poll, not a boot read
        ]
        servers = [
            _server(9.5, 760, "adopted", busy_reload_ms=760.0),  # diag after Server-Timing
            _server(9.4, 765, "foreign", status="relayed"),      # relay leg ignored
            _server(19.8, 150, "ok"),
            _server(29.7, 905, "none", status=200),
            _server(39.7, 800, "adopted"),
            _server(49.7, 820, "adopted"),
        ]
        result = check_mod.check(clients, servers, lambda row: True)
        ev = result["evidence"]
        self.assertEqual(result["status"], "pending")  # 2 < minimum
        self.assertEqual(ev["samples"], 2)
        self.assertEqual(ev["adopted_samples"], 1)
        self.assertEqual(ev["overlapping_reload"], 1)
        self.assertEqual([s["since"] for s in ev["slowest"]], ["adopted", "ok"])

    def test_pass_and_fail_on_p95(self):
        fast = [_client(10 * i, 200) for i in range(1, 7)]
        servers = [_server(10 * i - 0.3, 205, "adopted") for i in range(1, 7)]
        self.assertEqual(check_mod.check(fast, servers, lambda row: True)["status"], "pass")
        slow = fast + [_client(100, 650)]
        servers.append(_server(99.7, 660, "adopted"))
        self.assertEqual(check_mod.check(slow, servers, lambda row: True)["status"], "fail")

    def test_undeployed_builds_are_ignored(self):
        clients = [_client(10 * i, 900) for i in range(1, 7)]
        servers = [_server(10 * i - 0.3, 900, "adopted") for i in range(1, 7)]
        result = check_mod.check(clients, servers, lambda row: False)
        self.assertEqual(result["status"], "pending")
        self.assertFalse(result["real_data"])


if __name__ == "__main__":
    unittest.main()
