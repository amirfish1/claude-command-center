"""Coverage contract for the CCC introduction video.

Reads the shipped bound transcript (the narration / caption / on-screen copy
tied to the master video) and asserts every public inventory item from the
README is present by name and function, and that banned private / overstated
claims are absent.

The transcript file is the thing under test. This module does not re-implement
the narration, mock it, or hard-code expected sentences.
"""
from __future__ import annotations

from pathlib import Path
import importlib.util
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT = ROOT / "docs" / "intro-video" / "transcript.md"
MASTER = ROOT / "docs" / "intro-video" / "out" / "ccc-introduction.mp4"
SLIDES = ROOT / "docs" / "intro-video" / "slides.py"

# Each item is (display name that must appear, one or more function phrases
# that must also appear somewhere in the bound transcript). Names come from
# README Features, Also in the box, Decision Inbox, Engine support, the ccc
# CLI, WatchTower queues/workers, search, mobile / Simple Mode, and install.
INVENTORY = (
    # Identity
    ("local dashboard", ("attaches", "needs you")),
    # Engine support: eight spawnable
    ("Claude Code", ("spawn",)),
    ("Codex", ("spawn",)),
    ("Cursor", ("metadata-only",)),
    ("Antigravity", ("spawn",)),
    ("Kilo Code", ("fire-and-forget", "no resume")),
    ("Kimi Code", ("spawn",)),
    ("OpenCode", ("spawn",)),
    ("Devin", ("spawn",)),
    # Engine support: three read-only
    ("GitHub Copilot CLI", ("read-only",)),
    ("VS Code Copilot Chat", ("read-only",)),
    ("Grok CLI", ("read-only",)),
    # Features
    ("eight engines", ("on-disk",)),
    ("cost-aware cold-session composer", ("cheaper routes",)),
    ("FIRST FLIGHT", ("Settings",)),
    ("Settings modal", ("search",)),
    ("Plan-to-fleet", ("WatchTower", "queue")),
    ("ACP adapter", ("Agent Client Protocol",)),
    ("Project tree", ("objects",)),
    ("Flow canvas", ("zoom",)),
    ("two transcripts", ("side by side",)),
    ("GitHub", ("issue", "session")),
    ("worktree", ("feature branch",)),
    ("Headless spawn", ("input bar",)),
    ("resume-on-demand", ("dormant",)),
    ("Auto-fix deploys", ("Vercel",)),
    ("AI-assisted titles", ("Haiku",)),
    ("Claude Desktop", ("macOS",)),
    # Also in the box
    ("permission prompts", ("Approve",)),
    ("System status", ("health",)),
    ("orchestration skill", ("spawn", "inject", "ask")),
    ("Usage tracking", ("plan limits",)),
    ("status briefs", ("queue",)),
    ("GitHub-backed queues", ("issues",)),
    ("create a queue", ("session",)),
    # Decision Inbox
    ("Decision Inbox", ("three-option", "stalled")),
    ("token governor", ("Nudge", "Pause", "Kill")),
    ("strategy board", ("WatchTower",)),
    # ccc CLI
    ("ccc sessions", ("census",)),
    ("ccc models", ("effort",)),
    ("ccc quota", ("weekly",)),
    ("ccc doctor", ("health",)),
    ("ccc spawn", ("session",)),
    ("ccc send", ("messages",)),
    ("ccc ask", ("reply",)),
    # WatchTower queues and workers
    ("WatchTower", ("queue", "workers")),
    ("learnings file", ("worker",)),
    # Search
    ("Full-text search", ("zero setup",)),
    ("semantic", ("not on by default",)),
    # Mobile / Simple Mode
    ("Simple Mode", ("plain-language",)),
    ("trusted network", ("loopback",)),
    # Install
    ("curl", ("install",)),
    ("brew install ccc", ("install",)),
    ("DMG", ("macOS",)),
    ("live demo", ("seeded",)),
)

BANNED_PRIVATE = (
    "Morning view",
    "morning view",
    "ccc-voice",
    "car-mode",
    "car mode",
    "ccc-voice",
    "SSH multiplexer",
    "ssh_multiplexer",
    "kanban",
    "Kanban",
    "Board view",
    "ICEBOX",
)

# Overstatements that must not appear as positive claims. Qualifying
# (negated) wording is required elsewhere in INVENTORY.
BANNED_OVERSTATE = (
    "identical support for every engine",
    "identical support for all eight",
    "identical support for all engines",
    "semantic search by default",
    "semantic mode is on by default",
    "Kilo Code follow-up",
    "resume Kilo",
)

REQUIRED_VISUAL_SOURCES = (
    "intro-fleet-list.mp4",
    "intro-attention-list.mp4",
    "intro-spawn-list.mp4",
    "V-07-flow-canvas.mp4",
    "V-16-group-chat.mp4",
    "V-09-search.mp4",
    "V-15-mobile.mp4",
)

PRIVACY_PATTERNS = (
    re.compile(r"/Users/[A-Za-z]"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{8,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{8,}\b"),
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class TestIntroVideoCoverage(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            TRANSCRIPT.is_file(),
            f"bound transcript missing: {TRANSCRIPT}",
        )
        self.text = TRANSCRIPT.read_text(encoding="utf-8")
        self.folded = _norm(self.text)
        self.lower = self.folded.lower()

    def test_transcript_covers_readme_inventory_by_name_and_function(self):
        missing = []
        for name, functions in INVENTORY:
            if name not in self.folded and name.lower() not in self.lower:
                missing.append(f"name {name!r}")
                continue
            for phrase in functions:
                if (
                    phrase not in self.folded
                    and phrase.lower() not in self.lower
                ):
                    missing.append(f"{name!r} function {phrase!r}")
        self.assertEqual(missing, [], "inventory gaps in " + str(TRANSCRIPT))

    def test_banned_private_and_overstated_claims_are_absent(self):
        hits = [p for p in BANNED_PRIVATE if p in self.text]
        hits.extend(p for p in BANNED_OVERSTATE if p.lower() in self.lower)
        self.assertEqual(hits, [], "banned claims in transcript")

    def test_kilo_and_cursor_and_search_and_network_are_qualified(self):
        self.assertIn("fire-and-forget", self.lower)
        self.assertIn("no resume", self.lower)
        self.assertIn("metadata-only", self.lower)
        self.assertIn("not on by default", self.lower)
        self.assertIn("never", self.lower)
        self.assertIn("open internet", self.lower)
        # "open internet" is allowed only as a negation.
        for sentence in re.split(r"(?<=[.!?])\s+", self.folded):
            if "open internet" in sentence.lower():
                self.assertRegex(
                    sentence.lower(),
                    r"\b(never|not|do not|don't)\b",
                    f"unqualified open-internet claim: {sentence}",
                )
            if "identical support" in sentence.lower():
                self.assertRegex(
                    sentence.lower(),
                    r"\b(never|not|do not|don't|no)\b",
                    f"unqualified identical-support claim: {sentence}",
                )

    def test_scene_map_names_required_real_ui_sources(self):
        missing = [src for src in REQUIRED_VISUAL_SOURCES if src not in self.text]
        self.assertEqual(missing, [], "scene map missing real UI clip names")

    def test_transcript_has_no_private_paths_emails_or_tokens(self):
        hits = []
        for pat in PRIVACY_PATTERNS:
            found = pat.findall(self.text)
            hits.extend(found)
        self.assertEqual(hits, [], "privacy leak in transcript")

    def test_slides_narration_is_bound_into_the_transcript(self):
        """The renderer speaks slides.py; that copy must live in transcript.md."""
        self.assertTrue(SLIDES.is_file(), SLIDES)
        spec = importlib.util.spec_from_file_location("ccc_intro_slides", SLIDES)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        slides = module.SLIDES
        self.assertIsInstance(slides, list)
        self.assertGreaterEqual(len(slides), 16)
        missing = []
        for idx, slide in enumerate(slides):
            narration = _norm(slide.get("narration") or "")
            self.assertTrue(narration, f"slide {idx} has empty narration")
            if narration not in _norm(self.text):
                missing.append(f"slide {idx}")
        self.assertEqual(missing, [], "slides.py narration missing from transcript")

    def test_master_video_path_is_named(self):
        self.assertIn("ccc-introduction.mp4", self.text)

    def test_master_video_file_is_the_shipped_playable(self):
        self.assertTrue(MASTER.is_file(), MASTER)
        self.assertGreater(MASTER.stat().st_size, 1_000_000, MASTER)


if __name__ == "__main__":
    unittest.main()
