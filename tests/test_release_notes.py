"""Tests for scripts/release_notes.py against a fixture git log.

Parsing/classification/rendering are pure functions exercised directly
against fixture strings (no real git repo needed); one end-to-end test
builds a throwaway git repo to prove the CLI wiring works.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_notes as rn  # noqa: E402


FIXTURE_LOG = (
    "\x02aaa1111\x1ffeat(ui): add context-usage pill above the input bar\n"
    "static/app.js\nstatic/index.html\n"
    "\x02aaa2222\x1ffeat(ui): record context-usage pill hover details\n"
    "static/app.js\n"
    "\x02bbb2222\x1ffix(queue): steer on idle Codex thread no longer no-ops\n"
    "ccc_worker.py\n"
    "\x02bbb3333\x1ffix(queue): keep queued steer acknowledgements stable\n"
    "ccc_server/pending_inputs.py\n"
    "\x02ccc3333\x1ffix(restart): verify launchd owns this pid before trusting kickstart\n"
    "server.py\n"
    "\x02ddd4444\x1fchore(sprint): lane W2-1b ingest done\n"
    ".sprint/LANE.md\n"
    "\x02eee5555\x1ftest(queue): bring layout tests up to date with the queue UI redesign\n"
    "tests/test_queue_layout.py\n"
    "\x02fff6666\x1fperf(archive): incremental delta instead of full rebuild\n"
    "server.py\narchive.py\n"
    "\x02999bbbb\x1ffeat(ui): add sparkle border around session rows\n"
    "static/app.css\n"
    "\x02888cccc\x1ffeat(ui): remove COO board\n"
    "static/app.js\n"
    "\x02777aaaa\x1fnot a conventional commit message\n"
    "README.md\n"
)


def test_parse_conventional_splits_type_scope_desc():
    ctype, scope, desc = rn.parse_conventional("feat(ui): add a context pill")
    assert ctype == "feat"
    assert scope == "ui"
    assert desc == "add a context pill"


def test_parse_conventional_handles_missing_scope():
    ctype, scope, desc = rn.parse_conventional("docs: refresh readme")
    assert ctype == "docs"
    assert scope is None
    assert desc == "refresh readme"


def test_parse_conventional_falls_back_on_freeform_subject():
    ctype, scope, desc = rn.parse_conventional("wip stuff")
    assert ctype is None
    assert scope is None
    assert desc == "wip stuff"


def test_classify_feat_is_new():
    assert rn.classify("feat", "ui") == "new"


def test_classify_perf_is_improved():
    assert rn.classify("perf", "archive") == "improved"


def test_classify_fix_with_user_facing_scope_is_fixed():
    assert rn.classify("fix", "queue") == "fixed"


def test_classify_fix_with_internal_scope_is_under_the_hood():
    assert rn.classify("fix", "restart") == "under_the_hood"


def test_classify_chore_is_always_under_the_hood():
    assert rn.classify("chore", "sprint") == "under_the_hood"


def test_classify_unconventional_subject_is_under_the_hood():
    assert rn.classify(None, None) == "under_the_hood"


def test_parse_git_log_output_splits_records_and_files():
    commits = rn._parse_git_log_output(FIXTURE_LOG)
    assert [c.sha for c in commits] == [
        "aaa1111", "aaa2222", "bbb2222", "bbb3333", "ccc3333", "ddd4444",
        "eee5555", "fff6666", "999bbbb", "888cccc", "777aaaa",
    ]
    assert commits[0].subject == "feat(ui): add context-usage pill above the input bar"
    assert commits[0].files == ["static/app.js", "static/index.html"]
    assert commits[7].files == ["server.py", "archive.py"]
    assert commits[10].files == ["README.md"]


def test_group_commits_buckets_and_groups_by_scope():
    commits = rn._parse_git_log_output(FIXTURE_LOG)
    groups, under_the_hood = rn.group_commits(commits)

    assert len(groups["new"]) == 1
    assert groups["new"][0].scope == "ui"
    assert len(groups["new"][0].commits) == 2

    assert len(groups["improved"]) == 1
    assert groups["improved"][0].scope == "archive"

    assert len(groups["fixed"]) == 1
    assert groups["fixed"][0].scope == "queue"
    assert len(groups["fixed"][0].commits) == 2

    # chore(sprint), test(queue), fix(restart) [internal scope], and the
    # freeform commit all land in "under the hood". So do user-facing
    # commits that cannot earn a deterministic why.
    under_the_hood_shas = {c.sha for c in under_the_hood}
    assert under_the_hood_shas == {"ccc3333", "ddd4444", "eee5555", "999bbbb", "777aaaa"}


def test_capability_renders_what_why_how():
    commits = rn._parse_git_log_output(FIXTURE_LOG)
    groups, _ = rn.group_commits(commits)
    cap = groups["fixed"][0]

    assert "steer" in cap.what.lower()
    assert "no longer no-ops" in cap.what
    assert "acknowledgements stable" in cap.what
    assert cap.why == "keeps queues moving without you babysitting them"
    assert cap.how_files == ["ccc_worker.py", "ccc_server/pending_inputs.py"]


def test_render_markdown_has_value_first_headings_and_collapsed_under_the_hood():
    commits = rn._parse_git_log_output(FIXTURE_LOG)
    groups, under_the_hood = rn.group_commits(commits)
    md = rn.render_markdown(groups, under_the_hood, "v1.0.0..HEAD")

    assert "## New" in md
    assert "## Improved" in md
    assert "## Fixed" in md
    assert "**What:**" in md
    assert "**Why it matters:**" in md
    assert "adds a capability you didn't have before" not in md
    assert "sparkle border" not in md.split("<details>")[0]
    assert "COO board is gone" in md
    assert "remove COO board. **Why it matters:** adds a capability" not in md
    assert "<details>" in md
    assert "Under the hood (5 commits)" in md
    # Raw commit chatter should only appear inside the collapsed section.
    assert "chore(sprint)" not in md.split("<details>")[0]


def test_render_markdown_handles_empty_range():
    md = rn.render_markdown({"new": [], "improved": [], "fixed": []}, [], "HEAD..HEAD")
    assert "No user-facing capability changes" in md


def test_render_html_escapes_and_includes_sections():
    commits = rn._parse_git_log_output(FIXTURE_LOG)
    groups, under_the_hood = rn.group_commits(commits)
    out = rn.render_html(groups, under_the_hood, "v1.0.0..HEAD")

    assert "<h2>New</h2>" in out
    assert "<h2>Fixed</h2>" in out
    assert "<details>" in out
    assert "<code>ccc_worker.py</code>" in out
    assert "adds a capability you didn&#x27;t have before" not in out


def test_render_markdown_caps_sections_by_evidence_weight():
    groups = {"new": [], "improved": [], "fixed": [], "removed": []}
    for idx in range(14):
        commit = rn.Commit(
            sha=f"{idx:07x}",
            subject=f"feat(queue): queue capability {idx}",
            files=[f"file-{idx}-{n}.py" for n in range(idx % 4 + 1)],
        )
        parsed = rn.ParsedCommit(
            commit=commit,
            type="feat",
            scope="queue",
            desc=f"queue capability {idx}",
            bucket="new",
        )
        groups["new"].append(rn.Capability(bucket="new", scope="queue", commits=[parsed]))

    md = rn.render_markdown(groups, [], "fixture")

    assert md.count("- **What:**") == 12
    assert "- and 2 more New capabilities." in md


def test_polish_with_llm_falls_back_when_cli_missing(monkeypatch):
    monkeypatch.setattr(rn.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    md = "# unchanged\n"
    assert rn.polish_with_llm(md) == md


def test_cli_end_to_end_against_a_throwaway_git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *cmd: subprocess.run(cmd, cwd=repo, check=True, capture_output=True, text=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "test@example.com")
    run("git", "config", "user.name", "Test")

    (repo / "a.txt").write_text("1\n")
    run("git", "add", "a.txt")
    run("git", "commit", "-q", "-m", "feat(queue): add a queue widget")

    (repo / "b.txt").write_text("2\n")
    run("git", "add", "b.txt")
    run("git", "commit", "-q", "-m", "chore(deps): bump lockfile")

    commits = rn.get_commits(range_spec="HEAD", cwd=repo)
    assert len(commits) == 2

    groups, under_the_hood = rn.group_commits(commits)
    assert len(groups["new"]) == 1
    assert len(under_the_hood) == 1

    out_dir = tmp_path / "out"
    rc = rn.main([
        "--since", "1 second ago", "--out-dir", str(out_dir), "--stem", "test",
    ])
    # main() reads from ROOT's git repo (the real CCC checkout), not the
    # throwaway one above -- --since keeps it to a handful of commits (or
    # zero) instead of walking full history like a bare "HEAD" range would.
    assert rc == 0
    assert (out_dir / "test.md").exists()
    assert (out_dir / "test.html").exists()
