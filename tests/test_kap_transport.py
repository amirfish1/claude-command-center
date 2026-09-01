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
    """Envelope as it appears on the wire: the frame's own `type` is the event
    type. `session_event` is the AsyncAPI message name, not a wrapper that is
    ever sent literally -- observed frames put the type at both levels."""
    return {"type": payload.get("type"), "seq": seq, "epoch": epoch,
            "session_id": "session_x", "payload": payload}


class TestKapTranscriptMapper(unittest.TestCase):
    """The daemon streams `transcript.ops`, a small document protocol -- not
    the raw agent event union. Ops here mirror what 0.39.1 actually sent."""

    def setUp(self):
        self.m = kap.KapTranscriptMapper()

    def _ops(self, ops, seq=1, agent="main"):
        return self.m.feed(_frame({"type": "transcript.ops", "agent_id": agent,
                                   "ops": ops, "seq": seq}, seq=seq))

    def _run_turn(self, extra_ops=()):
        self._ops([{"op": "turn.upsert",
                    "turn": {"turnId": "t0", "state": "running"}}])
        self._ops(list(extra_ops))
        return self._ops([{"op": "turn.upsert",
                           "turn": {"turnId": "t0", "state": "completed"}}])

    def test_appends_stream_into_their_frame(self):
        events = self._run_turn([
            {"op": "frame.upsert",
             "frame": {"frameId": "t0.1.f1", "kind": "text", "text": ""}},
            {"op": "append", "target": {"frameId": "t0.1.f1"},
             "offset": 0, "text": "Hello"},
            {"op": "append", "target": {"frameId": "t0.1.f1"},
             "offset": 5, "text": " world"},
        ])
        self.assertEqual([e["type"] for e in events], ["assistant", "result"])
        self.assertEqual(events[0]["blocks"],
                         [{"kind": "text", "text": "Hello world"}])
        self.assertEqual(events[0]["message_id"], "kap-kimi-t0")

    def test_frames_keep_arrival_order(self):
        events = self._run_turn([
            {"op": "frame.upsert",
             "frame": {"frameId": "f1", "kind": "thinking", "text": ""}},
            {"op": "append", "target": {"frameId": "f1"},
             "offset": 0, "text": "hmm"},
            {"op": "frame.upsert",
             "frame": {"frameId": "f2", "kind": "text", "text": ""}},
            {"op": "append", "target": {"frameId": "f2"},
             "offset": 0, "text": "answer"},
        ])
        self.assertEqual(events[0]["blocks"], [
            {"kind": "thinking", "text": "hmm"},
            {"kind": "text", "text": "answer"},
        ])

    def test_frame_upsert_with_text_reconciles_rather_than_doubles(self):
        # The settled frame.upsert arrives again at turn end carrying the full
        # text; concatenating it would duplicate the whole message.
        events = self._run_turn([
            {"op": "frame.upsert",
             "frame": {"frameId": "f1", "kind": "text", "text": ""}},
            {"op": "append", "target": {"frameId": "f1"},
             "offset": 0, "text": "KAP_SPIKE_OK"},
            {"op": "frame.upsert",
             "frame": {"frameId": "f1", "kind": "text",
                       "text": "KAP_SPIKE_OK"}},
        ])
        self.assertEqual(events[0]["blocks"],
                         [{"kind": "text", "text": "KAP_SPIKE_OK"}])

    def test_offset_gap_is_repaired_not_appended_blind(self):
        events = self._run_turn([
            {"op": "frame.upsert",
             "frame": {"frameId": "f1", "kind": "text", "text": ""}},
            {"op": "append", "target": {"frameId": "f1"},
             "offset": 0, "text": "abcdef"},
            {"op": "append", "target": {"frameId": "f1"},
             "offset": 3, "text": "XYZ"},
        ])
        self.assertEqual(events[0]["blocks"],
                         [{"kind": "text", "text": "abcXYZ"}])

    def test_repeated_terminal_turn_upsert_emits_once(self):
        self._ops([{"op": "turn.upsert",
                    "turn": {"turnId": "t0", "state": "running"}},
                   {"op": "frame.upsert",
                    "frame": {"frameId": "f1", "kind": "text", "text": "hi"}}])
        first = self._ops([{"op": "turn.upsert",
                            "turn": {"turnId": "t0", "state": "completed"}}])
        second = self._ops([{"op": "turn.upsert",
                             "turn": {"turnId": "t0", "state": "completed"}}])
        self.assertEqual([e["type"] for e in first], ["assistant", "result"])
        self.assertEqual(second, [])

    def test_end_reason_from_meta_phase_wins(self):
        self._ops([{"op": "turn.upsert",
                    "turn": {"turnId": "t0", "state": "running"}}])
        self._ops([{"op": "meta.merge", "meta": {"agent": {"phase": {
            "kind": "ended", "reason": "cancelled"}}}}])
        events = self._ops([{"op": "turn.upsert",
                             "turn": {"turnId": "t0", "state": "completed"}}])
        self.assertEqual(events[-1]["subtype"], "cancelled")

    def test_usage_and_context_are_captured(self):
        self._ops([{"op": "meta.merge", "meta": {"agent": {
            "usage": {"total": {"output": 38}}, "contextTokens": 26080}}}])
        self.assertEqual(self.m.usage, {"total": {"output": 38}})
        self.assertEqual(self.m.context_tokens, 26080)

    def test_prompt_status_is_tracked(self):
        self._ops([{"op": "prompt.upsert",
                    "prompt": {"promptId": "p1", "status": "running"}}])
        self.assertEqual(self.m.prompts, {"p1": "running"})
        self._ops([{"op": "prompt.upsert",
                    "prompt": {"promptId": "p1", "status": "completed"}}])
        self.assertEqual(self.m.prompts, {"p1": "completed"})

    def test_subagent_ops_do_not_leak_into_main_transcript(self):
        self._ops([{"op": "turn.upsert",
                    "turn": {"turnId": "t0", "state": "running"}},
                   {"op": "frame.upsert",
                    "frame": {"frameId": "f1", "kind": "text",
                              "text": "mine"}}])
        self._ops([{"op": "frame.upsert",
                    "frame": {"frameId": "sf1", "kind": "text",
                              "text": "NOT MINE"}}], agent="sub-1")
        events = self._ops([{"op": "turn.upsert",
                             "turn": {"turnId": "t0", "state": "completed"}}])
        self.assertEqual(events[0]["blocks"],
                         [{"kind": "text", "text": "mine"}])

    def test_work_changed_tracks_busy_without_emitting(self):
        self.assertEqual(
            self.m.feed({"type": "event.session.work_changed",
                         "payload": {"busy": True}}), [])
        self.assertIs(self.m.busy, True)

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
        self.m.feed(_frame({"type": "transcript.ops", "agent_id": "main",
                            "ops": []}, seq=17, epoch="ep-9"))
        self.assertEqual(self.m.last_seq, 17)
        self.assertEqual(self.m.epoch, "ep-9")


class TestKapCapturedTurnReplay(unittest.TestCase):
    """Replay of a real turn captured off kap-server 0.39.1. This is the
    regression anchor: if a Kimi upgrade changes the op protocol, the recorded
    wire no longer reduces to the right conversation and this fails."""

    def test_captured_turn_reduces_to_one_assistant_message(self):
        fixture = (Path(__file__).parent / "fixtures"
                   / "kap_turn_frames.jsonl")
        mapper = kap.KapTranscriptMapper()
        events = []
        with fixture.open() as fh:
            for line in fh:
                if line.strip():
                    events.extend(mapper.feed(json.loads(line)))
        self.assertEqual([e["type"] for e in events], ["assistant", "result"])
        self.assertEqual(events[0]["blocks"], [
            {"kind": "thinking",
             "text": 'User asks to reply with exactly "KAP_SPIKE_OK". '
                     'Just do it.'},
            {"kind": "text", "text": "KAP_SPIKE_OK"},
        ])
        self.assertEqual(events[1], {"type": "result",
                                     "subtype": "completed"})
        self.assertEqual(mapper.context_tokens, 26080)
        self.assertEqual(mapper.usage["total"]["output"], 38)
        self.assertIs(mapper.busy, False)


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
