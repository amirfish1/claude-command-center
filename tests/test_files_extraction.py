"""Tests for the per-conversation file index (server-side extraction)."""

import importlib
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


class TestCategorize(unittest.TestCase):
    def setUp(self):
        # Re-import server fresh; some sibling tests mutate sys.modules.
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")

    def test_image_extensions_categorized_as_images(self):
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
                    ".heic", ".bmp", ".tiff"):
            with self.subTest(ext=ext):
                self.assertEqual(
                    self.server._categorize_file_target("/tmp/x" + ext),
                    "images",
                )

    def test_pdf_categorized(self):
        self.assertEqual(self.server._categorize_file_target("/x/a.pdf"), "pdfs")

    def test_uppercase_extensions_normalized(self):
        # Real conversations contain `.PNG`, `.PDF`, etc. Categorizer must
        # be case-insensitive on the extension.
        self.assertEqual(self.server._categorize_file_target("/x/a.PDF"), "pdfs")
        self.assertEqual(self.server._categorize_file_target("/x/Y.JPEG"), "images")

    def test_excluded_extensions_return_none(self):
        # Code/scripts MUST NOT categorize — they're the load-bearing
        # security clamp on /api/reveal-file. If an attacker convinces the
        # extractor a `.sh` is a file, the modal could render it and the
        # opener would shell out. The whitelist is closed by design.
        for ext in (".py", ".sh", ".js", ".ts", ".rb", ".go", ".rs", ".app",
                    ".command", ".workflow", ".applescript",
                    ".json", ".yaml", ".yml", ".toml", ".css", ".sql",
                    ".lock", ".txt"):
            with self.subTest(ext=ext):
                self.assertIsNone(
                    self.server._categorize_file_target("/tmp/x" + ext),
                    f"{ext} must NOT categorize — it would weaken the opener clamp",
                )

    def test_no_extension_returns_none(self):
        self.assertIsNone(self.server._categorize_file_target("/tmp/somefile"))
        self.assertIsNone(self.server._categorize_file_target("https://example.com/"))

    def test_url_with_known_extension_categorizes(self):
        self.assertEqual(
            self.server._categorize_file_target("https://drive.google.com/foo.pdf"),
            "pdfs",
        )


class TestExtractor(unittest.TestCase):
    def setUp(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")

        # Point _resolve_conversation_path at our fixture by patching
        # _conversation_dirs() to return the fixtures dir, where we
        # symlink/copy the fixture under the conversation-id name the
        # extractor expects. Simplest: monkey-patch the resolver itself.
        self.fixture = REPO / "tests" / "fixtures" / "files-extraction.jsonl"
        self._orig_resolve = self.server._resolve_conversation_path
        self.server._resolve_conversation_path = lambda cid: self.fixture

    def tearDown(self):
        self.server._resolve_conversation_path = self._orig_resolve

    def test_extracts_expected_files_per_category(self):
        result = self.server._extract_files_from_conversation("ignored")
        self.assertIn("groups", result)
        self.assertIn("count", result)
        self.assertFalse(result["truncated"])

        groups = result["groups"]

        def targets(cat):
            return [r["target"] for r in groups.get(cat, [])]

        self.assertEqual(set(targets("images")),
                         {"/Users/testuser/Desktop/diagram.png"})
        self.assertEqual(set(targets("pdfs")),
                         {"/Users/testuser/Apps/foo/notes.pdf",
                          "https://example.com/spec.pdf"})
        self.assertEqual(set(targets("presentations")),
                         {"/Users/testuser/Downloads/deck.pptx"})
        self.assertEqual(set(targets("videos")),
                         {"https://example.com/video.mp4"})
        self.assertEqual(set(targets("markdown")),
                         {"/Users/testuser/Apps/foo/intro.md"})
        self.assertEqual(set(targets("html")),
                         {"/Users/testuser/Apps/foo/report.html"})

        # Total == sum across non-empty groups.
        self.assertEqual(result["count"],
                         sum(len(v) for v in groups.values()))

    def test_excluded_extensions_never_appear(self):
        result = self.server._extract_files_from_conversation("ignored")
        all_targets = []
        for rows in result["groups"].values():
            all_targets.extend(r["target"] for r in rows)
        for t in all_targets:
            self.assertFalse(
                t.lower().endswith(".sh"),
                f"shell script leaked into extractor: {t}",
            )
            self.assertFalse(
                t.lower().endswith(".py"),
                f"python file leaked into extractor: {t}",
            )

    def test_de_duplicates_repeats(self):
        # `/Users/testuser/Apps/foo/intro.md` appears twice in the fixture
        # (tool_result + Bash command). Must collapse to one row.
        result = self.server._extract_files_from_conversation("ignored")
        md_targets = [r["target"] for r in result["groups"].get("markdown", [])]
        self.assertEqual(md_targets.count("/Users/testuser/Apps/foo/intro.md"), 1)

    def test_de_duplicated_repeats_track_last_line(self):
        result = self.server._extract_files_from_conversation("ignored")
        intro = next(
            r for r in result["groups"].get("markdown", [])
            if r["target"] == "/Users/testuser/Apps/foo/intro.md"
        )
        self.assertEqual(intro["first_line"], 3)
        self.assertEqual(intro["last_line"], 4)

    def test_each_row_has_label_target_kind_first_and_last_line(self):
        result = self.server._extract_files_from_conversation("ignored")
        for cat, rows in result["groups"].items():
            for r in rows:
                with self.subTest(cat=cat, target=r.get("target")):
                    self.assertIn("label", r)
                    self.assertIn("target", r)
                    self.assertIn("kind", r)
                    self.assertIn("first_line", r)
                    self.assertIn("last_line", r)
                    self.assertIn(r["kind"], ("path", "url"))

    def test_missing_jsonl_returns_empty(self):
        self.server._resolve_conversation_path = lambda cid: Path("/no/such/file.jsonl")
        result = self.server._extract_files_from_conversation("ignored")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["groups"], {})
        self.assertFalse(result["truncated"])


if __name__ == "__main__":
    unittest.main()


class _CountingJson:
    """json module proxy that counts loads() calls (call-count invariant,
    same spirit as tests/test_perf_budget.py)."""

    def __init__(self, real):
        self._real = real
        self.loads_calls = 0

    def loads(self, *a, **k):
        self.loads_calls += 1
        return self._real.loads(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


class TestExtractorIncremental(unittest.TestCase):
    """/api/conversations/<id>/files is refetched on every SSE tick of the
    open conversation. Re-walking the whole transcript each time held the
    interpreter for seconds on big sessions (measured 2026-09-12: 0.5-2 s
    JSONL, 8-15 s Devin). The extractor must resume from where it stopped."""

    def setUp(self):
        import shutil
        import tempfile
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "conv.jsonl"
        shutil.copy(REPO / "tests" / "fixtures" / "files-extraction.jsonl", self.path)
        self._orig_resolve = self.server._resolve_conversation_path
        self.server._resolve_conversation_path = lambda cid, repo_path=None: self.path
        self.cj = _CountingJson(self.server.json)
        self._orig_json = self.server.json
        self.server.json = self.cj

    def tearDown(self):
        self.server._resolve_conversation_path = self._orig_resolve
        self.server.json = self._orig_json
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _append(self, text):
        with open(self.path, "a") as fh:
            fh.write(text)

    def _line(self, text):
        return self._orig_json.dumps({"message": {"role": "user", "content": text}}) + "\n"

    def test_unchanged_file_decodes_nothing_on_second_call(self):
        first = self.server._extract_files_from_conversation("c1")
        self.assertGreater(self.cj.loads_calls, 0)
        self.cj.loads_calls = 0
        second = self.server._extract_files_from_conversation("c1")
        self.assertEqual(self.cj.loads_calls, 0)
        self.assertEqual(first, second)

    def test_appended_line_decodes_only_the_new_line(self):
        self.server._extract_files_from_conversation("c1")
        self.cj.loads_calls = 0
        self._append(self._line("see /Users/testuser/Desktop/new-file.pdf"))
        result = self.server._extract_files_from_conversation("c1")
        self.assertEqual(self.cj.loads_calls, 1)
        pdfs = {r["target"]: r for r in result["groups"].get("pdfs", [])}
        self.assertIn("/Users/testuser/Desktop/new-file.pdf", pdfs)
        self.assertEqual(pdfs["/Users/testuser/Desktop/new-file.pdf"]["first_line"], 6)
        # Earlier rows survive the incremental pass.
        self.assertIn("/Users/testuser/Apps/foo/notes.pdf", pdfs)
        self.assertEqual(result["count"], 8)

    def test_partial_trailing_line_is_not_consumed_until_complete(self):
        self.server._extract_files_from_conversation("c1")
        full = self._line("see /Users/testuser/Desktop/half.pdf")
        self._append(full[:20])
        self.cj.loads_calls = 0
        mid = self.server._extract_files_from_conversation("c1")
        self.assertEqual(self.cj.loads_calls, 0)
        self.assertEqual(mid["count"], 7)
        self._append(full[20:])
        result = self.server._extract_files_from_conversation("c1")
        self.assertEqual(self.cj.loads_calls, 1)
        pdfs = {r["target"]: r for r in result["groups"].get("pdfs", [])}
        self.assertEqual(pdfs["/Users/testuser/Desktop/half.pdf"]["first_line"], 6)

    def test_rewritten_shorter_file_reparses_from_scratch(self):
        self.server._extract_files_from_conversation("c1")
        self.path.write_text(self._line("only /Users/testuser/Desktop/solo.png"))
        result = self.server._extract_files_from_conversation("c1")
        self.assertEqual(result["count"], 1)
        self.assertEqual(
            [r["target"] for r in result["groups"]["images"]],
            ["/Users/testuser/Desktop/solo.png"],
        )


class TestDevinExtractorIncremental(unittest.TestCase):
    SCHEMA = """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            working_directory TEXT,
            created_at REAL,
            last_activity_at REAL
        );
        CREATE TABLE message_nodes (
            row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            node_id INTEGER NOT NULL,
            chat_message TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE(session_id, node_id)
        );
    """

    def setUp(self):
        import os
        import sqlite3
        import tempfile
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")
        import ccc_server.devin as devin_mod
        self.devin = devin_mod
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "sessions.db")
        con = sqlite3.connect(self.db)
        con.executescript(self.SCHEMA)
        con.execute(
            "INSERT INTO sessions (id, working_directory, created_at, last_activity_at) "
            "VALUES (?, ?, ?, ?)", ("alpha-one", "/tmp/x", 1.0, 1.0))
        for i in range(1, 4):
            con.execute(
                "INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) "
                "VALUES (?, ?, ?, ?)",
                ("alpha-one", i,
                 self._msg("user", f"open /Users/testuser/Desktop/doc{i}.pdf"), i))
        con.commit()
        con.close()
        prev = os.environ.get("CCC_DEVIN_DB")
        os.environ["CCC_DEVIN_DB"] = self.db
        self.addCleanup(lambda: (os.environ.__setitem__("CCC_DEVIN_DB", prev)
                                 if prev is not None
                                 else os.environ.pop("CCC_DEVIN_DB", None)))
        self._orig_json = devin_mod.json
        self.cj = _CountingJson(devin_mod.json)
        devin_mod.json = self.cj

    def tearDown(self):
        import shutil
        self.devin.json = self._orig_json
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _msg(self, role, content):
        import json
        return json.dumps({"role": role, "content": content, "metadata": {}})

    def _add_row(self, node_id, content):
        import sqlite3
        con = sqlite3.connect(self.db)
        con.execute(
            "INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) "
            "VALUES (?, ?, ?, ?)", ("alpha-one", node_id, self._msg("user", content), node_id))
        con.commit()
        con.close()

    def test_unchanged_session_decodes_nothing_on_second_call(self):
        first = self.devin._extract_files_from_devin_cli_conversation("devincli-alpha-one")
        self.assertEqual(first["count"], 3)
        self.cj.loads_calls = 0
        second = self.devin._extract_files_from_devin_cli_conversation("devincli-alpha-one")
        self.assertEqual(self.cj.loads_calls, 0)
        self.assertEqual(first, second)

    def test_new_row_decodes_only_the_new_row(self):
        self.devin._extract_files_from_devin_cli_conversation("devincli-alpha-one")
        self._add_row(4, "and /Users/testuser/Desktop/doc4.pdf")
        self.cj.loads_calls = 0
        result = self.devin._extract_files_from_devin_cli_conversation("devincli-alpha-one")
        self.assertEqual(self.cj.loads_calls, 1)
        self.assertEqual(result["count"], 4)
        pdfs = {r["target"]: r for r in result["groups"]["pdfs"]}
        self.assertEqual(pdfs["/Users/testuser/Desktop/doc4.pdf"]["first_line"], 4)
        self.assertEqual(pdfs["/Users/testuser/Desktop/doc1.pdf"]["first_line"], 1)
