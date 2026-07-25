"""Regression coverage for guarded Codex/Kimi bridge recovery."""

import importlib
import pathlib
import tempfile
import unittest
from unittest import mock

from ccc_worker import WorkerRuntime
from control_plane import WorkLedger
from worker_engines import EngineHost


class BridgeRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def setUp(self):
        server = self.server
        with server._pending_resume_lock:
            self.original_resume = dict(server._pending_resume_queue)
            server._pending_resume_queue.clear()
        with server._pending_terminal_input_lock:
            self.original_terminal = dict(server._pending_terminal_input_queue)
            server._pending_terminal_input_queue.clear()

    def tearDown(self):
        server = self.server
        with server._pending_resume_lock:
            server._pending_resume_queue.clear()
            server._pending_resume_queue.update(self.original_resume)
        with server._pending_terminal_input_lock:
            server._pending_terminal_input_queue.clear()
            server._pending_terminal_input_queue.update(self.original_terminal)

    def test_kimi_status_lists_other_active_sessions(self):
        server = self.server
        state = {
            "target": {"status": "active"},
            "other": {"status": "active"},
            "idle": {"status": "idle"},
        }
        with mock.patch.object(server, "_acp_load_state"), \
             mock.patch.object(server, "_ACP_SESSION_STATE", {"kimi": state}), \
             mock.patch.object(server, "_ACP_CONNS", {}):
            status = server._engine_bridge_status_local("kimi", "target")

        self.assertEqual(status["active_session_ids"], ["target", "other"])
        self.assertEqual(status["other_active_session_ids"], ["other"])
        self.assertTrue(status["shared"])
        self.assertTrue(status["owned"])

    def test_restart_refuses_while_another_kimi_session_is_active(self):
        server = self.server
        blocked = {
            "ok": True,
            "engine": "kimi",
            "bridge": "Kimi ACP",
            "other_active_session_ids": ["other"],
        }
        with mock.patch.object(
            server, "_engine_bridge_status_local", return_value=blocked,
        ), mock.patch.object(server, "_acp_cancel") as cancel:
            result = server._restart_engine_bridge_local("kimi", "target")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "bridge_in_use")
        self.assertEqual(result["other_active_session_ids"], ["other"])
        cancel.assert_not_called()

    def test_codex_status_describes_active_approval_sessions(self):
        server = self.server
        state = {
            "approval-thread": {
                "status": "active",
                "thread_needs_approval": True,
                "last_item": {"command": "git status"},
            },
            "working-thread": {"status": "active"},
        }
        with mock.patch.object(server, "_CODEX_APP_SERVER_THREAD_STATE", state), \
             mock.patch.object(server, "_CODEX_APP_SERVER_TRANSPORT", None), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZED", False):
            status = server._engine_bridge_status_local("codex", "")

        details = {
            row["session_id"]: row for row in status["active_sessions"]
        }
        self.assertTrue(details["approval-thread"]["needs_approval"])
        self.assertIn(
            "git status",
            details["approval-thread"]["needs_approval_message"],
        )
        self.assertFalse(details["working-thread"]["needs_approval"])

    def test_approval_blockers_use_worker_bridge_state(self):
        server = self.server

        def routed(engine, operation, args, mutate=False):
            self.assertEqual(operation, "bridge_status")
            return {
                "ok": True,
                "engine": engine,
                "active_sessions": (
                    [{
                        "session_id": "approval-thread",
                        "needs_approval": True,
                        "needs_approval_message": "Approve command?",
                    }, {
                        "session_id": "working-thread",
                        "needs_approval": False,
                    }] if engine == "codex" else []
                ),
            }

        with mock.patch.object(
            server, "_control_plane_engine_call", side_effect=routed,
        ):
            result = server._engine_bridge_approval_blockers()

        self.assertEqual(result, {
            "ok": True,
            "sessions": [{
                "session_id": "approval-thread",
                "engine": "codex",
                "needs_approval": True,
                "needs_approval_message": "Approve command?",
            }],
        })

    def test_selected_queue_message_is_retried_without_touching_others(self):
        server = self.server
        sid = "session-target"
        with server._pending_terminal_input_lock:
            server._pending_terminal_input_queue[sid] = ["first", "selected", "last"]
        restarted = {
            "ok": True,
            "engine": "kimi",
            "bridge": "Kimi ACP",
            "restarted": True,
        }
        delivered = {"ok": True, "via": "acp", "session_id": sid}
        with mock.patch.object(server, "_detect_session_engine", return_value="kimi"), \
             mock.patch.object(server, "_control_plane_engine_call", return_value=restarted), \
             mock.patch.object(server, "_inject_text_into_session", return_value=delivered) as inject, \
             mock.patch.object(server, "_save_pending_inputs"):
            result = server._recover_engine_bridge(sid, "selected", "recovery-key")

        self.assertTrue(result["ok"])
        self.assertTrue(result["retried"])
        inject.assert_called_once_with(
            sid, "selected", _from_terminal_queue=True, skip_wt=True,
        )
        with server._pending_terminal_input_lock:
            self.assertEqual(
                server._pending_terminal_input_queue[sid],
                ["first", "last"],
            )

    def test_failed_retry_returns_message_to_front(self):
        server = self.server
        sid = "session-target"
        with server._pending_terminal_input_lock:
            server._pending_terminal_input_queue[sid] = ["selected", "last"]
        restarted = {
            "ok": True,
            "engine": "codex",
            "bridge": "Codex app-server",
            "restarted": True,
        }
        with mock.patch.object(server, "_detect_session_engine", return_value="codex"), \
             mock.patch.object(server, "_control_plane_engine_call", return_value=restarted), \
             mock.patch.object(
                 server, "_inject_text_into_session",
                 return_value={"ok": False, "error": "transport failed"},
             ), mock.patch.object(server, "_save_pending_inputs"), \
             mock.patch.object(server, "_mark_terminal_queue_retry"):
            result = server._recover_engine_bridge(sid, "selected", "recovery-key")

        self.assertFalse(result["ok"])
        self.assertTrue(result["retry"]["requeued"])
        with server._pending_terminal_input_lock:
            self.assertEqual(
                server._pending_terminal_input_queue[sid],
                ["selected", "last"],
            )

    def test_recovery_ui_is_wired_to_transport_pill(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        html = (root / "static" / "index.html").read_text(encoding="utf-8")
        js = (root / "static" / "app.js").read_text(encoding="utf-8")
        css = (root / "static" / "app.css").read_text(encoding="utf-8")
        self.assertIn('id="bridgeRecoveryBackdrop"', html)
        self.assertIn("data-bridge-recovery", js)
        self.assertIn("/api/bridge-recovery/status", js)
        self.assertIn("/api/bridge-recovery/blockers", js)
        self.assertIn("Restart and retry", js)
        self.assertIn(".bridge-recovery-modal", css)

    def test_worker_owns_bridge_status_and_restart_operations(self):
        class FakeLegacy:
            def __init__(self):
                self.restart_calls = []

            @staticmethod
            def _engine_bridge_status_local(engine, sid):
                return {
                    "ok": True,
                    "engine": engine,
                    "session_id": sid,
                    "other_active_session_ids": [],
                }

            def _restart_engine_bridge_local(self, engine, sid):
                self.restart_calls.append((engine, sid))
                return {"ok": True, "engine": engine, "restarted": True}

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = WorkerRuntime(
                ledger=WorkLedger(pathlib.Path(temp_dir, "ledger.sqlite3")),
                token="a" * 64,
            )
            host = EngineHost(runtime)
            host._module = FakeLegacy()
            status = host.query({
                "engine": "kimi",
                "operation": "bridge_status",
                "args": {"session_id": "target"},
            })
            restarted = host.execute({
                "engine": "kimi",
                "operation": "bridge_restart",
                "idempotency_key": "recover-target",
                "args": {"session_id": "target"},
            })

        self.assertTrue(status["ok"])
        self.assertEqual(status["engine"], "kimi")
        self.assertTrue(restarted["ok"])
        self.assertEqual(restarted["work"]["state"], "completed")
        self.assertEqual(host._module.restart_calls, [("kimi", "target")])


if __name__ == "__main__":
    unittest.main()
