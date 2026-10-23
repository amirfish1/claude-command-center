"""Experimental idea tickets (ccc_server/idea_tickets.py).

Pure-module tests against a fake ``_core`` and a recorded ``_wt``: filing a
ticket for a UI-started session, inheriting it for continuations and child
sessions, skipping non-UI spawns, closing on archive of the latest session,
moving between queues, the flag gate on row stamping, and Jev's fallback.
No server.py, no real ``wt``.
"""

import importlib
import io
import json
import pathlib
import tempfile
import types
import unittest

it = importlib.import_module("ccc_server.idea_tickets")


class _Sync:
    """Run would-be daemon threads inline so assertions see their effects."""

    def __init__(self, target, args=(), daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


class IdeaTicketsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        (root / "markers").mkdir()
        self.flag = True
        self.registry = {}
        self.transcripts = {}
        self.calls = []
        self.next_ref = 1
        self._orig = (it._core, it._wt, it.threading.Thread)
        it._state_cache.update({"key": None, "data": None})
        it._queues_ready.clear()
        it._inflight.clear()

        def first_user_text(path):
            return self.transcripts.get(path, {}).get("text", "")

        self.core = types.SimpleNamespace(
            COMMAND_CENTER_STATE_DIR=root,
            SPAWN_MARKERS_DIR=root / "markers",
            _feature_flag=lambda name: self.flag and name == "idea_tickets",
            _spawn_registry_entry_for_session=lambda sid: self.registry.get(sid),
            _find_session_jsonl=lambda sid: sid if sid in self.transcripts else None,
            _continued_from_session_id_from_transcript=(
                lambda path: self.transcripts.get(path, {}).get("continued_from", "")),
            _extract_user_prompt_text=lambda ev: ev.get("text", ""),
            _log_activity=lambda *a: None,
            _restamp_archive_serve_cache_after_mutation=lambda: None,
        )
        it._core = self.core
        it._wt = self._fake_wt
        it.threading.Thread = _Sync
        it._first_user_text = first_user_text

    def tearDown(self):
        it._core, it._wt, it.threading.Thread = self._orig
        importlib.reload(it)
        self.tmp.cleanup()

    def _fake_wt(self, args, cwd=None, timeout=30):
        self.calls.append(list(args))
        if args[0] in ("take", "add"):
            queue = args[args.index("-q") + 1]
            ref = f"{queue}-{self.next_ref}"
            self.next_ref += 1
            return 0, f"FILED: {ref}  title\nCLAIMED: {ref} -> x\n"
        if args[0] == "edit" and "--queue" in args:
            queue = args[args.index("--queue") + 1]
            return 0, f"EDITED: {queue}-99 (moved)  title\n"
        return 0, ""

    def _ui_session(self, sid, text="Add a dark mode toggle"):
        self.registry[sid] = {"spawned_via": "ui", "cwd": "/tmp"}
        self.transcripts[sid] = {"text": text}
        it.note_ui_spawn(sid, "ui")

    def test_ui_session_gets_ticket_in_classified_queue(self):
        self._ui_session("s1", "The save button crashes")
        res = it.attach_or_file("s1", classify_fn=lambda text: "small_bug")
        self.assertEqual(res["ref"], "SMALL-BUGS-1")
        take = [c for c in self.calls if c[0] == "add"][0]
        self.assertNotIn("--worker", take)  # unclaimed: WT never renames the session
        self.assertEqual(take[take.index("--type") + 1], "bug")
        self.assertIn(["drain", "off", "SMALL-BUGS"], self.calls)
        row = it.idea_ticket_rows_map()["s1"]
        self.assertEqual((row["ref"], row["queue"], row["sessions"]), ("SMALL-BUGS-1", "SMALL-BUGS", 1))

    def test_completion_review_stays_scoped_to_reviewed_session(self):
        self._ui_session("s1")
        it.attach_or_file("s1", classify_fn=lambda text: "idea")
        it._mutate(lambda d: d["sessions"]["s1"].update(ready_to_close_reviewed_at=123))
        self.transcripts["s2"] = {"continued_from": "s1"}
        it.attach_or_file("s2", continued_from_hint="s1")
        rows = it.idea_ticket_rows_map()
        self.assertEqual(rows["s1"]["ready_to_close_reviewed_at"], 123)
        self.assertEqual(rows["s2"]["ready_to_close_reviewed_at"], 0)

    def test_pending_ui_spawn_rows_as_ideas_until_filed(self):
        self._ui_session("s1")
        row = it.idea_ticket_rows_map()["s1"]
        self.assertEqual((row["ref"], row["queue"], row["pending"]), ("", "IDEAS", True))
        it.attach_or_file("s1", classify_fn=lambda t: "small_bug")
        row = it.idea_ticket_rows_map()["s1"]
        self.assertEqual(row["ref"], "SMALL-BUGS-1")
        self.assertNotIn("pending", row)

    def test_continuation_and_child_inherit_and_log(self):
        self._ui_session("s1")
        it.attach_or_file("s1", classify_fn=lambda t: "idea")
        self.transcripts["s2"] = {"text": "You are continuing", "continued_from": "s1"}
        self.registry["s2"] = {"spawned_via": "ui"}
        self.assertEqual(it.attach_or_file("s2")["kind"], "continued")
        self.registry["s3"] = {"spawned_via": "cli", "parent_session_id": "s2"}
        self.transcripts["s3"] = {"text": "helper"}
        self.assertEqual(it.attach_or_file("s3")["kind"], "child")
        self.assertEqual(it.ticket_for_session("s3")["sessions"], ["s1", "s2", "s3"])
        comments = [c for c in self.calls if c[0] == "comment"]
        self.assertEqual(len(comments), 2)
        self.assertEqual(len([c for c in self.calls if c[0] == "add"]), 1)

    def test_non_ui_spawn_is_skipped(self):
        self.registry["w1"] = {"spawned_via": "cli"}
        self.transcripts["w1"] = {"text": "worker"}
        self.assertTrue(it.attach_or_file("w1")["skipped"])
        self.assertNotIn("w1", it.idea_ticket_rows_map())
        self.assertFalse([c for c in self.calls if c[0] == "add"])
        # Known sessions never re-run the lookup on later turns.
        self.assertEqual(it.on_turn_end("w1")["reason"], "known")

    def test_archive_latest_closes_earlier_only_logs_unarchive_reopens(self):
        self._ui_session("s1")
        it.attach_or_file("s1", classify_fn=lambda t: "idea")
        self.transcripts["s2"] = {"text": "cont", "continued_from": "s1"}
        it.attach_or_file("s2")
        it.on_archive("s1", True)
        self.assertFalse([c for c in self.calls if c[0] == "close"])
        it.on_archive("s2", True)
        self.assertTrue([c for c in self.calls if c[0] == "close"])
        self.assertTrue(it.ticket_for_session("s2")["closed"])
        it.on_archive("s2", False)
        self.assertTrue([c for c in self.calls if c[0] == "reopen"])
        self.assertFalse(it.ticket_for_session("s2")["closed"])

    def test_move_rewrites_ref_for_every_session(self):
        self._ui_session("s1")
        it.attach_or_file("s1", classify_fn=lambda t: "idea")
        self.transcripts["s2"] = {"text": "cont", "continued_from": "s1"}
        it.attach_or_file("s2")
        res = it.move_ticket("s2", "small-bugs")
        self.assertEqual((res["ref"], res["queue"]), ("SMALL-BUGS-99", "SMALL-BUGS"))
        self.assertEqual(it.ticket_for_session("s1")["ref"], "SMALL-BUGS-99")
        self.assertFalse(it.move_ticket("s1", "WATCH")["ok"])

    def test_move_to_other_detaches_and_refiles_on_return(self):
        self._ui_session("s1")
        it.attach_or_file("s1", classify_fn=lambda t: "idea")
        self.transcripts["s2"] = {"text": "cont", "continued_from": "s1"}
        it.attach_or_file("s2")
        res = it.move_ticket("s2", "other")
        self.assertEqual((res["ok"], res["queue"]), (True, "OTHER"))
        self.assertIn("close", [c[0] for c in self.calls])
        rows = it.idea_ticket_rows_map()
        self.assertNotIn("s1", rows)
        self.assertNotIn("s2", rows)
        # Skipped, so neither a sweep nor a turn end re-files it.
        self.assertEqual(it.on_turn_end("s2")["reason"], "known")
        res = it.move_ticket("s2", "IDEAS")
        self.assertEqual((res["ok"], res["queue"]), (True, "IDEAS"))
        self.assertEqual(it.idea_ticket_rows_map()["s2"]["ref"], res["ref"])

    def test_old_ui_session_is_not_backfilled(self):
        # spawned_via=ui in the registry, but spawned before the flag noted it.
        self.registry["old"] = {"spawned_via": "ui"}
        self.transcripts["old"] = {"text": "old work"}
        self.assertTrue(it.attach_or_file("old")["skipped"])
        it.note_ui_spawn("cli1", "cli")
        self.assertNotIn("cli1", it.load_state()["pending"])

    def test_ticketless_session_files_from_row_menu(self):
        self.registry["old"] = {"spawned_via": "terminal", "cwd": "/tmp"}
        self.transcripts["old"] = {"text": "The list flickers"}
        self.assertTrue(it.attach_or_file("old")["skipped"])  # recorded skip
        res = it.move_ticket("old", "SMALL-BUGS")
        self.assertEqual((res["ref"], res["queue"]), ("SMALL-BUGS-1", "SMALL-BUGS"))
        self.assertEqual(it.idea_ticket_rows_map()["old"]["ref"], "SMALL-BUGS-1")

    def test_backfill_files_oldest_first_and_continuations_join(self):
        it.classify = lambda text: "idea"
        self.registry["a"] = {"spawned_via": "terminal"}
        self.transcripts["a"] = {"text": "Build the thing"}
        self.transcripts["b"] = {"text": "You continue", "continued_from": "a"}
        self.transcripts["c"] = {"text": "Other work"}
        self.assertTrue(it.attach_or_file("c")["skipped"])
        res = it.backfill(["a", "b", "c", "agent-x", "a"])
        self.assertEqual(res["queued"], 3)
        self.assertEqual(it.ticket_for_session("b")["sessions"], ["a", "b"])
        self.assertEqual(it.ticket_for_session("c")["ref"], "IDEAS-2")
        self.assertEqual(len([c for c in self.calls if c[0] == "add"]), 2)
        self.assertEqual(it.load_state()["pending"], {})
        self.assertEqual(it.backfill(["a", "b", "c"])["queued"], 0)  # idempotent

    def test_late_filer_never_overwrites_a_filed_ticket(self):
        # Two processes sweep the same pending session: the archive-refresh
        # subprocess files it, then the server's thread arrives late.
        self._ui_session("s1")
        self.assertEqual(it.attach_or_file("s1", classify_fn=lambda t: "idea")["ref"], "IDEAS-1")
        self.assertTrue(it.attach_or_file("s1")["skipped"])  # no longer pending
        self.assertEqual(it.ticket_for_session("s1")["ref"], "IDEAS-1")
        # A live claim from another filer blocks a second ticket.
        self._ui_session("s2")
        it._mutate(lambda d: d["sessions"].__setitem__("s2", {"filing": 1, "at": int(it.time.time())}))
        self.assertTrue(it.attach_or_file("s2", classify_fn=lambda t: "idea")["known"])
        self.assertEqual(len([c for c in self.calls if c[0] == "add"]), 1)

    def test_sweep_retitles_ticket_from_session_title(self):
        self._ui_session("s1", "Can we fix this heads up: the VM cannot auto-update")
        ref = it.attach_or_file("s1", classify_fn=lambda t: "idea")["ref"]
        rows = [{"session_id": "s1", "display_name": "\U0001f9f5 IDEAS#1: Can we fix",
                 "auto_titled": True, "first_message": "Can we fix this"}]
        it.sweep_rows(rows)
        self.assertFalse([c for c in self.calls if c[0] == "edit"])  # WT badge name
        rows[0]["display_name"] = "bym-growth auto-update stall"
        it.sweep_rows(rows)
        self.assertIn(["edit", ref, "--title", "bym-growth auto-update stall"], self.calls)
        it.sweep_rows(rows)
        self.assertEqual(len([c for c in self.calls if c[0] == "edit"]), 1)

    def test_sweep_files_replied_pending_and_children_only(self):
        self._ui_session("c1")  # pending, replied
        self._ui_session("c2")  # pending, no reply yet
        rows = [
            {"session_id": "c1", "last_assistant_preview": "OK"},
            {"session_id": "c2", "last_event_type": "user"},
            {"session_id": "x9", "last_assistant_preview": "hi"},  # not a candidate
        ]
        self.assertEqual(it.sweep_rows(rows), 1)
        self.assertEqual(it.ticket_for_session("c1")["ref"], "IDEAS-1")
        self.assertNotIn("x9", it.load_state()["sessions"])
        self.assertIn("c2", it.load_state()["pending"])
        self.transcripts["k1"] = {"text": "child"}
        rows = [{"session_id": "k1", "parent_session_id": "c1", "last_assistant_preview": "done"}]
        self.assertEqual(it.sweep_rows(rows), 1)
        self.assertEqual(it.ticket_for_session("k1")["sessions"], ["c1", "k1"])
        self.assertEqual(it.sweep_rows(rows), 0)  # known now
        # Codex rows: no preview, but a finished turn ends on a result event.
        it.note_ui_spawn("cx1", "ui")
        rows = [{"session_id": "cx1", "last_event_type": "result", "first_message": "Fix the crash"}]
        self.assertEqual(it.sweep_rows(rows), 1)
        take = [c for c in self.calls if c[0] == "add"][-1]
        self.assertEqual(take[take.index("--title") + 1], "Fix the crash")

    def test_sweep_continuation_inherits_before_first_reply(self):
        self._ui_session("origin")
        ref = it.attach_or_file("origin", classify_fn=lambda t: "idea")["ref"]
        rows = [{"session_id": "continuation", "engine": "antigravity",
                 "continued_from_session_id": "origin", "last_event_type": "user"}]
        self.assertEqual(it.sweep_rows(rows), 1)
        ticket = it.ticket_for_session("continuation")
        self.assertEqual(ticket["ref"], ref)
        self.assertEqual(ticket["sessions"], ["origin", "continuation"])
        self.assertEqual(it.sweep_rows(rows), 0)
        self.assertEqual(len([c for c in self.calls if c[0] == "add"]), 1)

    def test_flag_off_disables_everything(self):
        self._ui_session("s1")
        it.attach_or_file("s1", classify_fn=lambda t: "idea")
        self.flag = False
        self.assertEqual(it.idea_ticket_rows_map(), {})
        self.assertEqual(it.on_turn_end("s9")["reason"], "disabled")
        self.calls.clear()
        it.on_archive("s1", True)
        self.assertEqual(self.calls, [])

    def test_classify_falls_back_to_idea(self):
        self.assertEqual(it.classify("crash on save", key=""), "idea")

        def boom(req, timeout=None):
            raise OSError("down")
        self.assertEqual(it.classify("crash on save", key="sk-ant-test-XXXX", opener=boom), "idea")

        def answer(choice):
            def opener(req, timeout=None):
                body = json.loads(req.data)
                self.assertIn("kind", body["questions"])
                return io.BytesIO(json.dumps({"answers": {"kind": {"choice": choice}}}).encode())
            return opener
        self.assertEqual(it.classify("crash", key="sk-ant-test-XXXX", opener=answer("small_bug")), "small_bug")
        self.assertEqual(it.classify("crash", key="sk-ant-test-XXXX", opener=answer("bogus")), "idea")


if __name__ == "__main__":
    unittest.main()
