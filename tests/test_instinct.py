"""Instinct daily brief (ccc_server/instinct.py).

The brief is only useful if it is trustworthy: it must see real commits, say
what it could not see instead of crashing, never file anything on its own, and
never render untrusted text (commit subjects, ticket titles) as HTML.
"""

import importlib
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

instinct = importlib.import_module("ccc_server.instinct")

NOW = 1_790_000_000.0
DAY = 86400


def _git(repo, *args, env=None):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env=env)


def _commit(repo, path, subject, author="Dev", ts=None, content=None):
    f = pathlib.Path(repo) / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text((content or subject) + "\n")
    env = dict(os.environ, GIT_AUTHOR_NAME=author, GIT_AUTHOR_EMAIL="dev@example.com",
               GIT_COMMITTER_NAME=author, GIT_COMMITTER_EMAIL="dev@example.com")
    if ts:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{int(ts)} +0000"
    _git(repo, "add", path, env=env)
    _git(repo, "commit", "-q", "-m", subject, env=env)


def _cfg(**over):
    cfg = json.loads(json.dumps(instinct.DEFAULTS))
    cfg["ccc_url"] = "http://127.0.0.1:9"
    cfg.update(over)
    return cfg


def _commit_rec(sha, subject, files, ts=NOW - 3600, author="Dev"):
    c = {"sha": sha * 5, "short": sha, "author": author, "ts": ts,
         "subject": subject, "files": files}
    c.update(instinct.parse_conventional(subject))
    return c


def _snapshot(repos=None, sessions=None, wt=None, queue_for_repo=None):
    return {"schema": 1, "generated_ts": NOW, "since_ts": NOW - DAY,
            "repos": repos or [], "sessions": sessions or [],
            "wt": wt or {"status": [], "blocked": [], "gated": []},
            "blind_spots": [], "queue_for_repo": queue_for_repo or {}}


def _repo_rec(label="app", commits=None, **kw):
    r = {"path": f"/work/{label}", "label": label, "source": "config",
         "commits": commits or [], "branch": "main", "upstream": "origin/main",
         "ahead": 0, "behind": 0, "dirty": 0, "oldest_unpushed_ts": None,
         "error": None, "hunch": {"decisions": [], "constraints": []},
         "hunch_present": False}
    r.update(kw)
    return r


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def make_repo(self, name="app"):
        repo = self.tmp / name
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        return repo


class ConventionalCommitTests(unittest.TestCase):
    def test_parses_type_scope_and_breaking(self):
        c = instinct.parse_conventional("feat(ui)!: new picker")
        self.assertEqual((c["type"], c["scope"], c["breaking"], c["desc"]),
                         ("feat", "ui", True, "new picker"))

    def test_unknown_or_freeform_is_other(self):
        self.assertEqual(instinct.parse_conventional("Merge stuff")["type"], "other")
        self.assertEqual(instinct.parse_conventional("wip: thing")["type"], "other")


class GitCollectorTests(TmpCase):
    def test_collects_window_commits_and_status(self):
        repo = self.make_repo()
        _commit(repo, "old.txt", "chore: ancient", ts=NOW - 10 * DAY)
        _commit(repo, "a.py", "fix(core): first", ts=NOW - 3600)
        _commit(repo, "b.py", "feat: second", ts=NOW - 1800)
        (repo / "dirty.txt").write_text("x")
        info = instinct.collect_git({"path": str(repo), "label": "app"}, NOW - DAY, _cfg())
        self.assertIsNone(info["error"])
        self.assertEqual([c["subject"] for c in info["commits"]], ["feat: second", "fix(core): first"])
        self.assertEqual(info["commits"][1]["files"], ["a.py"])
        self.assertEqual(info["commits"][1]["type"], "fix")
        self.assertEqual(info["branch"], "main")
        self.assertEqual(info["dirty"], 1)

    def test_ignores_bots_and_rebased_duplicates(self):
        repo = self.make_repo()
        _commit(repo, "base.txt", "chore: base", ts=NOW - 10 * DAY)
        _commit(repo, "a.py", "fix: real", ts=NOW - 3600)
        _commit(repo, "s.svg", "chore: update star history", author="github-actions[bot]", ts=NOW - 3000)
        _git(repo, "checkout", "-q", "-b", "rebased", "HEAD~2")
        env = dict(os.environ, GIT_COMMITTER_DATE=f"@{int(NOW - 500)} +0000",
                   GIT_COMMITTER_NAME="Dev", GIT_COMMITTER_EMAIL="dev@example.com")
        _git(repo, "cherry-pick", "main~1", env=env)  # keeps the author date
        info = instinct.collect_git({"path": str(repo), "label": "app"}, NOW - DAY, _cfg())
        subjects = [c["subject"] for c in info["commits"]]
        self.assertEqual(subjects, ["fix: real"])

    def test_same_subject_distinct_commits_are_kept(self):
        repo = self.make_repo()
        _commit(repo, "a.py", "wip", ts=NOW - 3600, content="one")
        _commit(repo, "a.py", "wip", ts=NOW - 1800, content="two")
        info = instinct.collect_git({"path": str(repo), "label": "app"}, NOW - DAY, _cfg())
        self.assertEqual(len(info["commits"]), 2)

    def test_unborn_branch_header(self):
        repo = self.make_repo()
        info = instinct.collect_git({"path": str(repo), "label": "app"}, NOW - DAY, _cfg())
        self.assertEqual(info["branch"], "main")

    def test_counts_unpushed_commits_against_upstream(self):
        remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
        repo = self.make_repo()
        _commit(repo, "a.py", "feat: base", ts=NOW - 3 * DAY)
        _git(repo, "remote", "add", "origin", str(remote))
        _git(repo, "push", "-q", "-u", "origin", "main")
        _commit(repo, "b.py", "feat: local only", ts=NOW - 2 * DAY)
        info = instinct.collect_git({"path": str(repo), "label": "app"}, NOW - DAY, _cfg())
        self.assertEqual(info["upstream"], "origin/main")
        self.assertEqual(info["ahead"], 1)
        self.assertEqual(info["oldest_unpushed_ts"], int(NOW - 2 * DAY))

    def test_missing_repo_reports_error_not_exception(self):
        info = instinct.collect_git({"path": str(self.tmp / "nope"), "label": "x"}, NOW - DAY, _cfg())
        self.assertTrue(info["error"])
        self.assertEqual(info["commits"], [])


class HunchTests(TmpCase):
    def write(self, repo, kind, rec):
        d = repo / ".hunch" / kind
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{rec['id']}.json").write_text(json.dumps(rec))

    def test_why_matches_anchored_decisions_and_scoped_invariants(self):
        repo = self.tmp / "r"
        self.write(repo, "decisions", {
            "id": "dec_a", "title": "Keep server stdlib-only", "status": "accepted",
            "decision": "No pip deps at runtime.", "related_files": ["server.py"],
            "alternatives_rejected": ["Use requests"], "valid_from": "2026-09-01T00:00:00Z",
            "provenance": {"confidence": 0.9}})
        self.write(repo, "decisions", {
            "id": "dec_old", "title": "Superseded", "status": "superseded",
            "decision": "x", "related_files": ["server.py"]})
        self.write(repo, "decisions", {
            "id": "dec_closed", "title": "Closed", "status": "accepted", "decision": "x",
            "related_files": ["server.py"], "valid_to": "2026-09-02T00:00:00Z"})
        self.write(repo, "constraints", {
            "id": "con_s", "statement": "Restart both services", "scope": ["server.py"],
            "severity": "warning", "status": "active"})
        self.write(repo, "constraints", {
            "id": "con_g", "statement": "Global rule", "scope": ["**"],
            "severity": "warning", "status": "active"})
        graph = instinct.load_hunch(str(repo))
        why = instinct.hunch_why(graph, ["server.py", "other.py"])
        self.assertEqual([d["id"] for d in why["decisions"]], ["dec_a"])
        self.assertEqual(why["decisions"][0]["rejected"], ["Use requests"])
        self.assertEqual([c["id"] for c in why["constraints"]], ["con_s"])

    def test_boilerplate_decisions_rank_last(self):
        graph = {"decisions": [
            {"id": "d1", "title": "auto", "decision": "Changed code in a.js (1 file(s)).",
             "related_files": ["a.js"]},
            {"id": "d2", "title": "real", "decision": "Chose X over Y because Z.",
             "related_files": ["a.js"], "alternatives_rejected": ["Y"]},
        ], "constraints": []}
        why = instinct.hunch_why(graph, ["a.js"])
        self.assertEqual([d["id"] for d in why["decisions"]], ["d2", "d1"])
        self.assertLess(why["decisions"][1]["weight"], 0)

    def test_repo_without_hunch_is_empty(self):
        g = instinct.load_hunch(str(self.tmp))
        self.assertFalse(g["present"])
        self.assertEqual(instinct.hunch_why(g, ["a"]), {"decisions": [], "constraints": []})


class DiscoverReposTests(TmpCase):
    def test_config_first_then_active_ccc_repos(self):
        a, b, c = self.make_repo("a"), self.make_repo("b"), self.make_repo("c")
        payload = {"repos": [
            {"path": str(b), "label": "b", "score": 5, "signals": {"d7": {"sessions": 2}}},
            {"path": str(c), "label": "c", "score": 9, "signals": {"d7": {"sessions": 0}}},
            {"path": str(a), "label": "a-dup", "score": 99, "signals": {"d7": {"sessions": 3}}},
            {"path": str(self.tmp / "not-git"), "label": "x", "score": 50,
             "signals": {"d7": {"sessions": 3}}},
        ]}
        repos = instinct.discover_repos(_cfg(repos=[str(a)], exclude_repo_globs=[]), payload)
        self.assertEqual([(r["label"], r["source"]) for r in repos], [("a", "config"), ("b", "ccc")])

    def test_tolerates_null_signals_and_missing_path(self):
        a = self.make_repo("a")
        payload = {"repos": [{"label": "nopath", "signals": {"d7": {"sessions": 5}}},
                             {"path": str(a), "signals": {"d7": {"sessions": None}}},
                             {"path": str(a), "score": None, "signals": None}]}
        cwd = os.getcwd()
        try:
            os.chdir(a)  # a missing path must not resolve to the cwd repo
            self.assertEqual(instinct.discover_repos(_cfg(exclude_repo_globs=[]), payload), [])
        finally:
            os.chdir(cwd)

    def test_exclude_globs_and_cap(self):
        a, b = self.make_repo("a"), self.make_repo("b")
        cfg = _cfg(repos=[str(a), str(b)], max_repos=1, exclude_repo_globs=[])
        self.assertEqual(len(instinct.discover_repos(cfg)), 1)
        cfg = _cfg(repos=[str(a)], exclude_repo_globs=[str(self.tmp) + "/*"])
        self.assertEqual(instinct.discover_repos(cfg), [])


class AnalyzeTests(unittest.TestCase):
    def test_changed_groups_by_type_and_counts(self):
        repo = _repo_rec(commits=[
            _commit_rec("aaaa1111", "feat(ui): picker", ["ui.js"]),
            _commit_rec("bbbb2222", "fix(ui): crash", ["ui.js"]),
            _commit_rec("cccc3333", "random words", ["x.txt"]),
        ])
        brief = instinct.analyze(_snapshot(repos=[repo]), _cfg())
        (c,) = brief["changed"]
        self.assertEqual(c["count"], 3)
        self.assertEqual([g["type"] for g in c["groups"]], ["feat", "fix", "other"])
        self.assertEqual(c["hot_files"][0], {"file": "ui.js", "commits": 2})
        self.assertEqual(brief["totals"]["commits"], 3)

    def test_stuck_sources_and_severity_order(self):
        sessions = [
            {"kind": "soft_block", "session_id": "s-soft", "name": "lane A",
             "mtime": NOW - 600, "next_step": "Pick one", "repo": "app"},
            {"kind": "pending_tool", "session_id": "s-tool", "name": "lane B",
             "mtime": NOW - 60, "repo": "app"},
        ]
        wt = {"status": [
            {"queue": "OPS", "depth": 4, "since_progress_s": 5 * DAY, "auto_drain": False,
             "oldest_open_age": "9d", "oldest_open_age_s": 9 * DAY},
            {"queue": "FRESH", "depth": 4, "since_progress_s": 3600, "auto_drain": True},
            {"queue": "EMPTY", "depth": 0, "since_progress_s": 90 * DAY},
            # A ticket filed 5h ago into a queue idle for months is not stalled.
            {"queue": "NEWTICKET", "depth": 1, "since_progress_s": 90 * DAY,
             "oldest_open_age_s": 5 * 3600},
        ], "blocked": [
            {"ref": "OPS-1", "title": "Need approval", "queue": "OPS",
             "block_question": "Ship it?", "blocked_at": "2026-09-21T00:00:00Z"},
            {"ref": "OPS-0", "title": "Ancient", "queue": "OPS",
             "blocked_at": "2026-06-01T00:00:00Z"},
        ], "gated": [{"ref": "OPS-9", "title": "Pitch", "queue": "OPS"}]}
        repo = _repo_rec(ahead=2, oldest_unpushed_ts=NOW - DAY)
        brief = instinct.analyze(_snapshot(repos=[repo], sessions=sessions, wt=wt), _cfg())
        kinds = [s["kind"] for s in brief["stuck"]]
        self.assertEqual(kinds[:2], ["blocked", "pending_tool"])  # high first, oldest first
        self.assertIn("queue_stalled", kinds)
        self.assertIn("unpushed", kinds)
        self.assertIn("gated", kinds)
        self.assertEqual(kinds.count("queue_stalled"), 1)  # FRESH/EMPTY/NEWTICKET are fine
        self.assertEqual(brief["stuck"][-1]["kind"], "blocked_stale")
        blocked_next = [n for n in brief["next"] if n["action"] == "Answer OPS-1"]
        self.assertEqual(blocked_next[0]["command"], "wt answer OPS-1")
        self.assertTrue(any(n["action"].startswith("Sweep 1 long-blocked") for n in brief["next"]))
        self.assertEqual(brief["totals"]["stuck_high"], 2)

    def test_proposals_are_dry_run_wt_commands(self):
        fixes = [_commit_rec(f"f{i}f{i}f{i}f{i}", f"fix: bug {i}", ["core.py", "tests/test_core.py"])
                 for i in range(3)]
        revert = _commit_rec("rrrr0000", 'Revert "feat: risky"', ["core.py"])
        repo = _repo_rec(commits=fixes + [revert])
        brief = instinct.analyze(_snapshot(repos=[repo], queue_for_repo={"/work/app": "APP"}), _cfg())
        titles = [p["title"] for p in brief["proposals"]]
        self.assertIn("Fix hotspot: core.py (3 fixes)", titles)
        self.assertFalse(any("test_core" in t for t in titles))  # tests aren't hotspots
        self.assertTrue(any(t.startswith("Re-land or close out reverted change") for t in titles))
        hot = next(p for p in brief["proposals"] if p["title"].startswith("Fix hotspot"))
        self.assertEqual(hot["queue"], "APP")
        self.assertTrue(hot["wt_command"].startswith("wt add -q APP --title "))
        self.assertTrue(hot["wt_command"].endswith("--priority p2"))
        for p in brief["proposals"]:
            self.assertRegex(p["wt_command"], r"--priority p[0-4]$")

    def test_unmapped_queue_uses_placeholder(self):
        fixes = [_commit_rec(f"f{i}f{i}f{i}f{i}", f"fix: bug {i}", ["core.py"]) for i in range(3)]
        brief = instinct.analyze(_snapshot(repos=[_repo_rec(commits=fixes)]), _cfg())
        self.assertIsNone(brief["proposals"][0]["queue"])
        self.assertIn("-q '<QUEUE>'", brief["proposals"][0]["wt_command"])

    def test_hunch_drift_is_one_proposal_per_repo(self):
        decisions = [{"id": f"dec_{i}", "title": f"Decision {i}", "decision": "d",
                      "files": ["ui.js"], "rejected": [], "date": "2026-09-01",
                      "verified_ts": NOW - 30 * DAY, "weight": 1.0} for i in range(5)]
        repo = _repo_rec(commits=[_commit_rec("aaaa1111", "feat: x", ["ui.js"])],
                         hunch={"decisions": decisions, "constraints": []})
        brief = instinct.analyze(_snapshot(repos=[repo]), _cfg())
        drift = [p for p in brief["proposals"] if p["key"].startswith("hunch-drift:")]
        self.assertEqual(len(drift), 1)
        self.assertIn("Re-verify 5 Hunch decision(s)", drift[0]["title"])

    def test_decision_recorded_after_change_is_not_drift(self):
        d = {"id": "dec_new", "title": "New", "decision": "d", "files": ["ui.js"],
             "rejected": [], "date": "", "verified_ts": NOW, "weight": 1.0}
        repo = _repo_rec(commits=[_commit_rec("aaaa1111", "feat: x", ["ui.js"])],
                         hunch={"decisions": [d], "constraints": []})
        brief = instinct.analyze(_snapshot(repos=[repo]), _cfg())
        self.assertEqual(brief["proposals"], [])

    def test_malformed_feed_items_do_not_crash(self):
        sessions = [{"kind": "soft_block", "session_id": None, "mtime": "soon"},
                    "not-a-dict", {"kind": "brand_new_kind", "mtime": None}]
        wt = {"status": [{"queue": "Q", "depth": "3", "since_progress_s": "x"}, 7],
              "blocked": ["OPS-1"], "gated": None}
        brief = instinct.analyze(_snapshot(sessions=sessions, wt=wt), _cfg())
        self.assertEqual(len(brief["stuck"]), 2)
        self.assertEqual(brief["stuck"][0]["title"], "session: Ended its turn waiting on you")
        self.assertEqual(brief["stuck"][1]["severity"], "low")  # unknown kind

    def test_low_signal_session_kinds_stay_out_of_next(self):
        sessions = [{"kind": k, "session_id": f"s{i}", "name": f"lane {i}", "mtime": NOW}
                    for i, k in enumerate(["pushed_open", "uncommitted_edits", "open_backlog"])]
        brief = instinct.analyze(_snapshot(sessions=sessions), _cfg())
        self.assertEqual(len(brief["stuck"]), 3)
        self.assertEqual(brief["next"], [])

    def test_memory_marks_repeat_proposals(self):
        fixes = [_commit_rec(f"f{i}f{i}f{i}f{i}", f"fix: bug {i}", ["core.py"]) for i in range(3)]
        snap = _snapshot(repos=[_repo_rec(commits=fixes)])
        first = instinct.analyze(snap, _cfg())
        memory = instinct.next_state({}, first)
        memory["proposals"] = {k: {"first": v["first"] - DAY, "last": v["last"] - DAY}
                               for k, v in memory["proposals"].items()}
        second = instinct.analyze(snap, _cfg(), memory)
        self.assertFalse(first["proposals"][0]["seen_before"])
        self.assertTrue(second["proposals"][0]["seen_before"])
        self.assertEqual(second["totals"]["proposals_new"], 0)

    def test_memory_keeps_a_proposal_seen_while_it_recurs(self):
        fixes = [_commit_rec(f"f{i}f{i}f{i}f{i}", f"fix: bug {i}", ["core.py"]) for i in range(3)]
        state, flags = {}, []
        for day in range(10):
            snap = _snapshot(repos=[_repo_rec(commits=fixes)])
            snap["generated_ts"] = NOW + day * DAY
            brief = instinct.analyze(snap, _cfg(), state)
            flags.append(brief["proposals"][0]["seen_before"])
            state = instinct.next_state(state, brief)
        self.assertEqual(flags, [False] + [True] * 9)
        # ...and it is forgotten once it stops recurring for a week.
        quiet = _snapshot()
        quiet["generated_ts"] = NOW + 20 * DAY
        self.assertEqual(instinct.next_state(state, instinct.analyze(quiet, _cfg(), state))["proposals"], {})

    def test_reads_schema1_float_memory(self):
        fixes = [_commit_rec(f"f{i}f{i}f{i}f{i}", f"fix: bug {i}", ["core.py"]) for i in range(3)]
        snap = _snapshot(repos=[_repo_rec(commits=fixes)])
        key = instinct.analyze(snap, _cfg())["proposals"][0]["key"]
        brief = instinct.analyze(snap, _cfg(), {"proposals": {key: NOW - DAY}})
        self.assertTrue(brief["proposals"][0]["seen_before"])

    def test_empty_snapshot_is_calm(self):
        brief = instinct.analyze(_snapshot(), _cfg())
        self.assertEqual(brief["headline"], "0 commit(s) across 0 repo(s), nothing stuck.")
        self.assertEqual(brief["next"], [])


class RenderTests(unittest.TestCase):
    def test_untrusted_text_is_escaped(self):
        evil = "<script>alert(1)</script>"
        repo = _repo_rec(commits=[_commit_rec("aaaa1111", f"feat: {evil}", ["a.py"])])
        wt = {"status": [], "gated": [], "blocked": [
            {"ref": "X-1", "title": evil, "block_question": f"<img src=x onerror=1>",
             "blocked_at": "2026-09-21T00:00:00Z"}]}
        out = instinct.render_html(instinct.analyze(_snapshot(repos=[repo], wt=wt), _cfg()))
        self.assertNotIn("<script>alert", out)
        self.assertNotIn("<img src=x", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("noindex", out)
        self.assertIn("Read-only", out)

    def test_sections_present(self):
        out = instinct.render_html(instinct.analyze(_snapshot(), _cfg()))
        for heading in ("What to do next", "What's stuck", "What changed", "Proposed tickets"):
            self.assertIn(heading, out)


class RunBriefTests(TmpCase):
    def test_writes_private_files_and_advances_state(self):
        out = self.tmp / "out"
        fixes = [_commit_rec(f"f{i}f{i}f{i}f{i}", f"fix: bug {i}", ["core.py"]) for i in range(3)]
        snap = _snapshot(repos=[_repo_rec(commits=fixes)])
        brief = instinct.run_brief(_cfg(), out, snapshot=snap, live=True)
        html_path = pathlib.Path(brief["html_path"])
        self.assertTrue(html_path.is_file())
        self.assertTrue((out / "latest.html").is_file())
        self.assertEqual(stat.S_IMODE(html_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o700)
        props = json.loads(next(out.glob("proposals-*.json")).read_text())
        self.assertEqual(len(props), 1)
        state = instinct.load_state(out)
        self.assertEqual(state["last_run_ts"], NOW)
        self.assertIn(props[0]["key"], state["proposals"])

    def test_window_starts_at_last_run_within_bounds(self):
        cfg = _cfg()
        self.assertEqual(instinct.window_start({"last_run_ts": NOW - 3600}, cfg, NOW), NOW - 3600)
        self.assertEqual(instinct.window_start({}, cfg, NOW), NOW - DAY)
        # A long gap is capped at max_window_hours (a week), not reset to a day.
        self.assertEqual(instinct.window_start({"last_run_ts": NOW - 30 * DAY}, cfg, NOW), NOW - 7 * DAY)
        self.assertEqual(instinct.window_start({"last_run_ts": NOW - 60}, cfg, NOW, 48), NOW - 2 * DAY)

    def test_window_does_not_advance_on_git_error_or_replay(self):
        out = self.tmp / "out"
        (out).mkdir()
        (out / "state.json").write_text(json.dumps({"last_run_ts": NOW - DAY}))
        broken = _snapshot(repos=[_repo_rec(error="git log: timed out after 20s")])
        instinct.run_brief(_cfg(), out, snapshot=broken, live=True)
        self.assertEqual(instinct.load_state(out)["last_run_ts"], NOW - DAY)
        instinct.run_brief(_cfg(), out, snapshot=_snapshot())  # replay
        self.assertEqual(instinct.load_state(out)["last_run_ts"], NOW - DAY)
        instinct.run_brief(_cfg(), out, snapshot=_snapshot(), live=True)
        self.assertEqual(instinct.load_state(out)["last_run_ts"], NOW)

    def test_caller_directories_keep_their_permissions(self):
        shared = self.tmp / "shared"
        shared.mkdir(mode=0o755)
        os.chmod(shared, 0o755)
        self.assertEqual(instinct.main(["init-config", "--config", str(shared / "c.json")]), 0)
        self.assertEqual(stat.S_IMODE(shared.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((shared / "c.json").stat().st_mode), 0o600)

    def test_publish_hook_returns_last_line_as_url(self):
        script = self.tmp / "pub.py"
        script.write_text("import sys\nprint('uploading', sys.argv[1])\n"
                          "print('https://pages.example.test/' + sys.argv[2].replace(' ', '-'))\n")
        cfg = _cfg(publish_command=[sys.executable, str(script), "{html}", "{title}"])
        brief = instinct.run_brief(cfg, self.tmp / "out", snapshot=_snapshot(), do_publish=True)
        self.assertTrue(brief["published_url"].startswith("https://pages.example.test/Instinct-brief-"))
        self.assertIsNone(brief["publish_error"])

    def test_publish_is_off_unless_configured(self):
        self.assertEqual(instinct.publish(_cfg(), self.tmp / "x.html", "t"), (None, None))
        self.assertEqual(instinct.publish(_cfg(publish_command="rm -rf /"), self.tmp / "x.html", "t")[1],
                         "publish_command must be a list of strings")


class CollectTests(TmpCase):
    def test_unreachable_sources_become_blind_spots(self):
        repo = self.make_repo()
        _commit(repo, "a.py", "feat: hi", ts=time.time() - 60)
        cfg = _cfg(repos=[str(repo)], exclude_repo_globs=[], wt_bin=str(self.tmp / "no-wt"))
        snap = instinct.collect(cfg, time.time() - DAY)
        self.assertEqual(len(snap["repos"]), 1)
        self.assertEqual(len(snap["repos"][0]["commits"]), 1)
        spots = " | ".join(snap["blind_spots"])
        self.assertIn("CCC repo list", spots)
        self.assertIn("CCC sessions", spots)
        self.assertIn("WatchTower", spots)

    def test_subprocess_count_is_bounded_per_repo(self):
        """Perf gate: git work is O(repos), never O(commits or files)."""
        repos = [self.make_repo(f"r{i}") for i in range(3)]
        for r in repos:
            for j in range(5):
                _commit(r, f"f{j}.py", f"fix: {j}", ts=time.time() - 60)
        cfg = _cfg(repos=[str(r) for r in repos], exclude_repo_globs=[],
                   wt_bin=str(self.tmp / "no-wt"))
        real = instinct._run
        calls = []
        with mock.patch.object(instinct, "_run", side_effect=lambda *a, **k: calls.append(a) or real(*a, **k)):
            instinct.collect(cfg, time.time() - DAY, use_ccc=False)
        git_calls = [c for c in calls if c[0][0] == "git"]
        self.assertLessEqual(len(git_calls), 3 * len(repos))
        self.assertLessEqual(len(calls) - len(git_calls), 3)  # wt status/blocked/gated


class ConfigTests(TmpCase):
    def test_defaults_and_user_overrides(self):
        path = self.tmp / "instinct.json"
        path.write_text(json.dumps({"repos": ["~/x"], "window_hours": 6,
                                    "repo_queues": {"~/x": "X"}}))
        with mock.patch.dict(os.environ, {"CCC_URL": "http://127.0.0.1:1234"}):
            cfg = instinct.load_config(path)
        home = os.path.expanduser("~")
        self.assertEqual(cfg["repos"], [os.path.join(home, "x")])
        self.assertEqual(cfg["repo_queues"], {os.path.join(home, "x"): "X"})
        self.assertEqual(cfg["window_hours"], 6)
        self.assertEqual(cfg["max_repos"], instinct.DEFAULTS["max_repos"])
        self.assertEqual(cfg["ccc_url"], "http://127.0.0.1:1234")

    def test_init_config_refuses_to_overwrite(self):
        path = self.tmp / "instinct.json"
        self.assertEqual(instinct.main(["init-config", "--config", str(path)]), 0)
        self.assertEqual(json.loads(path.read_text())["publish_command"], None)
        self.assertEqual(instinct.main(["init-config", "--config", str(path)]), 1)


if __name__ == "__main__":
    unittest.main()
