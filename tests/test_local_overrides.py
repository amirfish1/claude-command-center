"""Per-install names reach the same behaviour through local-overrides.json."""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
from unittest import mock

import pytest

import server  # noqa: F401  (registers the core that github_issues binds at import)
from ccc_server import github_issues, jobs_add, local_overrides, scheduled_jobs_feed, ship_graph

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_STATE_DIR", str(tmp_path))

    def write(data):
        path = tmp_path / "local-overrides.json"
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        os.utime(path, ns=(path.stat().st_mtime_ns + 1000, path.stat().st_mtime_ns + 1000))

    return write


def test_no_file_means_every_lookup_is_empty(overrides):
    assert local_overrides.repo_project_map() == {}
    assert local_overrides.repo_aliases() == {}
    assert local_overrides.ticket_title_noise() == []
    assert local_overrides.commit_scopes() == {}
    assert local_overrides.remote_apps_dir() == ""


@pytest.mark.parametrize("bad", ["{not json", "[]", '{"repo_project_map": 3, "repo_aliases": [],'
                                 ' "ticket_title_noise": {}, "remote_apps_dir": "relative"}'])
def test_malformed_config_is_ignored(overrides, bad):
    overrides(bad)
    assert local_overrides.repo_project_map() == {}
    assert local_overrides.repo_aliases() == {}
    assert local_overrides.ticket_title_noise() == []
    assert local_overrides.remote_apps_dir() == ""


def test_edited_file_is_reread(overrides):
    overrides({"remote_apps_dir": "/srv/one"})
    assert local_overrides.remote_apps_dir() == "/srv/one"
    overrides({"remote_apps_dir": "/srv/three/"})
    assert local_overrides.remote_apps_dir() == "/srv/three"


def test_repo_alias_through_config(overrides):
    assert ship_graph.detect_named_repo("did the acme digest ship")[0] is None
    overrides({"repo_aliases": {"Acme": [r"\bacme\b", "[invalid"]}})
    assert ship_graph.detect_named_repo("did the acme digest ship") == ("Acme", {"acme"})
    assert ship_graph.detect_named_repo("is ccc up")[0] == "claude-command-center"


def test_config_alias_repo_is_not_matched_twice_by_root_name(overrides):
    overrides({"repo_aliases": {"Acme": [r"\bacme-only\b"]}})
    assert ship_graph.detect_named_repo("the acme repo", {"Acme": "/srv/Acme"})[0] is None


def test_page_title_noise_through_config(overrides):
    title, text = "Acme: Home", "Fix the checkout button\nmore"
    assert ship_graph._clean_ticket_title(title, text) == title
    overrides({"ticket_title_noise": ["Acme: Home"]})
    assert ship_graph._clean_ticket_title(title, text) == "Fix the checkout button"


def test_commit_scope_through_config_longest_prefix_wins(overrides):
    assert github_issues._ship_commit_scope("apps/acme-ios/App.swift") == "apps"
    overrides({"commit_scopes": {"apps/acme": "acme", "apps/acme-ios": "ios"}})
    assert github_issues._ship_commit_scope("apps/acme-ios/App.swift") == "ios"
    assert github_issues._ship_commit_scope("apps/acme/page.tsx") == "acme"
    assert github_issues._ship_commit_scope("packages/ui/x.ts") == "ui"


def test_job_repo_folder_through_config(overrides):
    fields = {"host": "hermes", "repo": "Acme", "what": "x", "when": "daily"}
    overrides({"remote_apps_dir": "/srv/agent/Apps"})
    assert jobs_add.validate_job_add(fields, [])[0]["repo"] == "/srv/agent/Apps/Acme"
    full = dict(fields, repo="/srv/agent/Apps/Acme")
    assert jobs_add.validate_job_add(full, [])[0]["repo"] == "/srv/agent/Apps/Acme"


def test_job_repo_folder_defaults_to_home_apps(overrides):
    fields = {"host": "hermes", "repo": "Acme", "what": "x", "when": "daily"}
    expected = os.path.expanduser("~/Apps/Acme")
    assert jobs_add.validate_job_add(fields, [])[0]["repo"] == expected


def test_api_config_serves_the_repo_project_map(overrides):
    overrides({"repo_project_map": {"Acme+Web": "ACMEPROD"}})
    with mock.patch.object(server, "_app_config_cache", None):
        assert server.get_app_config()["repo_project_map"] == {"acme+web": "ACMEPROD"}


def test_host_labels_are_real_host_names(monkeypatch):
    feed = scheduled_jobs_feed
    assert feed.parse_reported_hostname("x\n@@CCCJOB:HOSTNAME build-box\n@@CCCJOB:END\n") == "build-box"
    assert feed.parse_reported_hostname("no marker") == ""
    assert "@@CCCJOB:HOSTNAME" in feed.REMOTE_SCRIPT
    monkeypatch.setattr(feed.socket, "gethostname", lambda: "this-box.example.ts.net")
    monkeypatch.setitem(feed._STATE, "hermes", dict(feed._STATE["hermes"], name="build-box"))
    with mock.patch.object(feed.platform, "system", return_value="Darwin"):
        hosts = feed.build_payload()["hosts"]
    assert hosts["hermes"]["label"] == "build-box"
    assert hosts["laptop"]["label"] == "this-box"
    monkeypatch.setitem(feed._STATE, "hermes", {k: v for k, v in feed._STATE["hermes"].items() if k != "name"})
    with mock.patch.object(feed.platform, "system", return_value="Linux"):
        hosts = feed.build_payload()["hosts"]
    assert hosts["hermes"]["label"] == "this-box"
    assert hosts["laptop"]["label"] == ""


def test_hostname_marker_does_not_disturb_job_parsing():
    text = "@@CCCJOB:HOSTNAME build-box\n@@CCCJOB:END\n"
    assert scheduled_jobs_feed.parse_hermes_output(text) == []


# ── scripts/check-private-terms.py ─────────────────────────────────────────

def _checker():
    spec = importlib.util.spec_from_file_location("check_private_terms", ROOT / "scripts" / "check-private-terms.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="n", GIT_AUTHOR_EMAIL="n@example.org",
               GIT_COMMITTER_NAME="n", GIT_COMMITTER_EMAIL="n@example.org")
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _run(repo, list_path, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["CCC_PRIVATE_TERMS_FILE"] = str(list_path)
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "check-private-terms.py"), *args],
                          cwd=repo, env=env, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    (path / "a.txt").write_text("nothing here\n")
    (path / "CHANGELOG.md").write_text("- old note about Zebra-Corp\n")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "init")
    return path


@pytest.fixture
def term_list(tmp_path):
    path = tmp_path / "terms.txt"
    path.write_text("# comment line\n\nzebra-corp\nsecret-box => example-host\n")
    return path


def test_list_format(term_list):
    assert _checker().load_terms([str(term_list)]) == ["zebra-corp", "secret-box"]


def test_gate_skips_without_a_list(repo, tmp_path):
    res = _run(repo, tmp_path / "missing.txt")
    assert res.returncode == 0 and "skipping" in res.stdout


def test_gate_passes_clean_tree_and_exempts_changelog(repo, term_list):
    res = _run(repo, term_list)
    assert res.returncode == 0, res.stdout
    assert "clean" in res.stdout


def test_gate_fails_on_new_match_without_printing_it(repo, term_list):
    (repo / "b.py").write_text("x = 1\nhost = 'SECRET-BOX'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "add b")
    res = _run(repo, term_list)
    assert res.returncode == 1
    assert "b.py:2 (term #2)" in res.stdout
    assert "secret-box" not in res.stdout.lower()


def test_gate_reads_the_pushed_tree_not_the_working_copy(repo, term_list):
    (repo / "a.txt").write_text("uncommitted zebra-corp\n")
    assert _run(repo, term_list).returncode == 0


def test_gate_checks_file_names_and_commit_messages(repo, term_list):
    (repo / "zebra-corp.md").write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "note for secret-box")
    res = _run(repo, term_list, "--rev", "HEAD", "--log", "HEAD~1..HEAD")
    assert res.returncode == 1
    assert "file name" in res.stdout and "commit message" in res.stdout


def test_pre_push_runs_the_gate():
    source = (ROOT / "scripts" / "pre-push.sh").read_text(encoding="utf-8")
    assert "scripts/check-private-terms.py" in source
