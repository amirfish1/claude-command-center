"""kap-server (Kimi daemon) transport — event mapping and daemon discovery.

The frames here mirror packages/kap-server/src/protocol/events-zod.ts exactly;
if a Kimi upgrade renames a field, these fail rather than the mapper silently
emitting empty turns. The pinned docs/kimi-kap/asyncapi.json is the reference.
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from ccc_server import kap


def _frame(payload, seq=1, epoch="e1"):
    return {"type": "session_event", "seq": seq, "epoch": epoch,
            "session_id": "session_x", "payload": payload}


class TestKapTurnMapper(unittest.TestCase):
    def setUp(self):
        self.m = kap.KapTurnMapper()

    def _feed(self, payloads):
        out = []
        for i, p in enumerate(payloads):
            out.extend(self.m.feed(_frame(p, seq=i + 1)))
        return out

    def test_deltas_fold_into_one_assistant_message(self):
        events = self._feed([
            {"type": "turn.started", "agentId": "main", "turnId": 7,
             "origin": {"kind": "user"}},
            {"type": "thinking.delta", "agentId": "main", "turnId": 7,
             "delta": "let me think"},
            {"type": "assistant.delta", "agentId": "main", "turnId": 7,
             "delta": "Hello"},
            {"type": "assistant.delta", "agentId": "main", "turnId": 7,
             "delta": " world"},
            {"type": "turn.ended", "agentId": "main", "turnId": 7,
             "reason": "completed"},
        ])
        self.assertEqual([e["type"] for e in events], ["assistant", "result"])
        msg = events[0]
        self.assertEqual(msg["message_id"], "kap-kimi-7")
        self.assertEqual(msg["blocks"], [
            {"kind": "thinking", "text": "let me think"},
            {"kind": "text", "text": "Hello world"},
        ])
        self.assertEqual(events[1]["subtype"], "completed")

    def test_subagent_events_do_not_leak_into_main_transcript(self):
        events = self._feed([
            {"type": "turn.started", "agentId": "main", "turnId": 1,
             "origin": {"kind": "user"}},
            {"type": "assistant.delta", "agentId": "main", "turnId": 1,
             "delta": "mine"},
            {"type": "assistant.delta", "agentId": "sub-1", "turnId": 4,
             "delta": "NOT MINE"},
            {"type": "turn.ended", "agentId": "sub-1", "turnId": 4,
             "reason": "completed"},
            {"type": "turn.ended", "agentId": "main", "turnId": 1,
             "reason": "completed"},
        ])
        self.assertEqual([e["type"] for e in events], ["assistant", "result"])
        self.assertEqual(events[0]["blocks"],
                         [{"kind": "text", "text": "mine"}])

    def test_tool_result_folds_onto_its_call_block(self):
        self._feed([
            {"type": "turn.started", "agentId": "main", "turnId": 2,
             "origin": {"kind": "user"}},
            {"type": "tool.call.started", "agentId": "main", "turnId": 2,
             "toolCallId": "tc1", "name": "Bash", "args": {"cmd": "ls"},
             "description": "list files"},
            {"type": "tool.result", "agentId": "main", "turnId": 2,
             "toolCallId": "tc1", "output": "a\nb", "isError": False},
        ])
        events = self.m.feed(_frame(
            {"type": "turn.ended", "agentId": "main", "turnId": 2,
             "reason": "completed"}))
        block = events[0]["blocks"][0]
        self.assertEqual(block["kind"], "tool_use")
        self.assertEqual(block["name"], "Bash")
        self.assertEqual(block["input"], {"cmd": "ls"})
        self.assertEqual(block["description"], "list files")
        self.assertEqual(block["status"], "ok")
        self.assertEqual(block["output"], "a\nb")

    def test_tool_error_marks_status(self):
        self._feed([
            {"type": "turn.started", "agentId": "main", "turnId": 3,
             "origin": {"kind": "user"}},
            {"type": "tool.call.started", "agentId": "main", "turnId": 3,
             "toolCallId": "t9", "name": "Bash", "args": {}},
            {"type": "tool.result", "agentId": "main", "turnId": 3,
             "toolCallId": "t9", "output": "boom", "isError": True},
        ])
        events = self.m.feed(_frame(
            {"type": "turn.ended", "agentId": "main", "turnId": 3,
             "reason": "completed"}))
        self.assertEqual(events[0]["blocks"][0]["status"], "error")

    def test_abort_flushes_partial_turn_as_cancelled(self):
        events = self._feed([
            {"type": "turn.started", "agentId": "main", "turnId": 5,
             "origin": {"kind": "user"}},
            {"type": "assistant.delta", "agentId": "main", "turnId": 5,
             "delta": "partial"},
            {"type": "prompt.aborted", "agentId": "main", "promptId": "p1",
             "abortedAt": "2026-09-01T00:00:00.000Z"},
        ])
        self.assertEqual([e["type"] for e in events], ["assistant", "result"])
        self.assertEqual(events[0]["blocks"],
                         [{"kind": "text", "text": "partial"}])
        self.assertEqual(events[1]["subtype"], "cancelled")

    def test_control_frames_are_not_conversation_events(self):
        for ftype in ("server_hello", "ack", "ping", "pong"):
            self.assertEqual(self.m.feed({"type": ftype}), [])

    def test_resync_required_is_surfaced_not_swallowed(self):
        events = self.m.feed({"type": "resync_required", "payload": {
            "session_id": "session_x", "reason": "buffer_overflow",
            "current_seq": 42}})
        self.assertEqual(events, [{"type": "result",
                                   "subtype": "resync_required"}])

    def test_seq_and_epoch_tracked_for_resume(self):
        self.m.feed(_frame({"type": "turn.started", "agentId": "main",
                            "turnId": 1, "origin": {"kind": "user"}},
                           seq=17, epoch="ep-9"))
        self.assertEqual(self.m.last_seq, 17)
        self.assertEqual(self.m.epoch, "ep-9")


class TestKapHeartbeat(unittest.TestCase):
    """kap-server's heartbeat is an application frame, not the RFC 6455 ping
    opcode, and it drops the socket after two missed replies. Answering it is
    what keeps a streaming turn alive past 20 seconds."""

    def test_ping_gets_a_pong_carrying_the_nonce(self):
        reply = kap.kap_heartbeat_reply(
            {"type": "ping", "timestamp": "2026-09-01T00:00:00.000Z",
             "payload": {"nonce": "n-123"}})
        self.assertEqual(reply, {"type": "pong", "payload": {"nonce": "n-123"}})

    def test_ping_without_nonce_still_replies(self):
        reply = kap.kap_heartbeat_reply({"type": "ping"})
        self.assertEqual(reply, {"type": "pong", "payload": {"nonce": ""}})

    def test_non_ping_frames_are_not_answered(self):
        for frame in ({"type": "server_hello"}, {"type": "session_event"},
                      {"type": "ack"}, {}, None, "ping"):
            self.assertIsNone(kap.kap_heartbeat_reply(frame))


class TestKapDiscovery(unittest.TestCase):
    def _home(self, tmp, rec):
        inst = Path(tmp) / "server" / "instances"
        inst.mkdir(parents=True)
        (inst / "srv.json").write_text(json.dumps(rec))
        os.environ[kap._KAP_HOME_ENV] = tmp

    def tearDown(self):
        os.environ.pop(kap._KAP_HOME_ENV, None)

    def test_live_instance_is_adopted(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._home(tmp, {"pid": os.getpid(), "host": "127.0.0.1",
                             "port": 58627,
                             "heartbeat_at": int(time.time() * 1000)})
            self.assertEqual(kap.kap_endpoint(), ("127.0.0.1", 58627))

    def test_stale_heartbeat_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale = int((time.time() - 10 * 60) * 1000)
            self._home(tmp, {"pid": os.getpid(), "host": "127.0.0.1",
                             "port": 58627, "heartbeat_at": stale})
            with self.assertRaises(kap.KapUnavailable):
                kap.kap_endpoint()

    def test_dead_pid_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._home(tmp, {"pid": 999999, "host": "127.0.0.1",
                             "port": 58627,
                             "heartbeat_at": int(time.time() * 1000)})
            with self.assertRaises(kap.KapUnavailable):
                kap.kap_endpoint()

    def test_missing_registry_is_unavailable_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ[kap._KAP_HOME_ENV] = tmp
            self.assertIsNone(kap.kap_discover())
            self.assertFalse(kap.kap_available())


if __name__ == "__main__":
    unittest.main()
