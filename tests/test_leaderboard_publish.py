import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "publish-leaderboard.sh"
SEED = ROOT / "site" / "leaderboard"


def run(*args):
    return subprocess.run(["bash", str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, timeout=10)


@pytest.fixture
def source(tmp_path):
    directory = tmp_path / "source with spaces"
    directory.mkdir()
    for name in ("index.html", "data.json"):
        (directory / name).write_bytes((SEED / name).read_bytes())
    return directory


@pytest.mark.parametrize("flags", [[], ["--dry-run"]])
def test_dry_run_does_not_create_destination_or_mutate_source(tmp_path, source, flags):
    destination = tmp_path / "not created" / "leaderboard"
    before = {f.name: f.read_bytes() for f in source.iterdir()}
    result = run(*flags, "--source-dir", source, "--dest-dir", destination)
    assert result.returncode == 0, result.stderr
    assert "Dry run. No files changed." in result.stdout
    assert result.stdout.count("Would copy") == 2
    assert not destination.parent.exists()
    assert {f.name: f.read_bytes() for f in source.iterdir()} == before


def test_apply_copies_only_two_frozen_files_and_preserves_unrelated_files(tmp_path, source):
    destination = tmp_path / "pages with spaces" / "leaderboard"
    destination.mkdir(parents=True)
    (destination / "keep.txt").write_text("DO_NOT_TOUCH")
    (source / "private.json").write_text("PRIVATE_SENTINEL")
    result = run("--apply", "--source-dir", source, "--dest-dir", destination)
    assert result.returncode == 0, result.stderr
    assert "Nothing was uploaded" in result.stdout
    assert {f.name for f in destination.iterdir()} == {"index.html", "data.json", "keep.txt"}
    for name in ("index.html", "data.json"):
        assert (destination / name).read_bytes() == (source / name).read_bytes()
    assert (destination / "keep.txt").read_text() == "DO_NOT_TOUCH"


def test_missing_source_json_prevents_any_destination_write(tmp_path, source):
    (source / "data.json").unlink()
    destination = tmp_path / "new destination"
    result = run("--apply", "--source-dir", source, "--dest-dir", destination)
    assert result.returncode != 0
    assert not destination.exists()


@pytest.mark.parametrize("content", ["{bad json", "{}", '{"private": "PRIVATE_SENTINEL"}'])
def test_invalid_snapshot_does_not_replace_destination(tmp_path, source, content):
    (source / "data.json").write_text(content)
    destination = tmp_path / "pages"
    destination.mkdir()
    (destination / "index.html").write_text("PREVIOUS_PAGE")
    (destination / "data.json").write_text("PREVIOUS_DATA")
    result = run("--apply", "--source-dir", source, "--dest-dir", destination)
    assert result.returncode != 0
    assert (destination / "index.html").read_text() == "PREVIOUS_PAGE"
    assert (destination / "data.json").read_text() == "PREVIOUS_DATA"
    assert "PRIVATE_SENTINEL" not in result.stderr


def test_private_extra_field_fails_before_destination_is_created(tmp_path, source):
    payload = json.loads((source / "data.json").read_text())
    payload["router"] = "PRIVATE_SENTINEL"
    (source / "data.json").write_text(json.dumps(payload))
    destination = tmp_path / "new pages"
    assert run("--apply", "--source-dir", source, "--dest-dir", destination).returncode != 0
    assert not destination.exists()


@pytest.mark.parametrize("kind", ["source-directory", "source-file", "dest-directory", "dest-file", "dest-parent"])
def test_symlinks_are_rejected_and_targets_untouched(tmp_path, source, kind):
    destination = tmp_path / "pages"
    target = tmp_path / "untouched"
    target.mkdir()
    (target / "index.html").write_text("UNCHANGED")
    (target / "data.json").write_bytes((SEED / "data.json").read_bytes())
    if kind == "source-directory":
        alias = tmp_path / "source alias"
        alias.symlink_to(source, target_is_directory=True)
        source = alias
    elif kind == "source-file":
        (source / "index.html").unlink()
        (source / "index.html").symlink_to(target / "index.html")
    elif kind == "dest-directory":
        destination.symlink_to(target, target_is_directory=True)
    elif kind == "dest-file":
        destination.mkdir()
        (destination / "index.html").symlink_to(target / "index.html")
    else:
        alias = tmp_path / "parent alias"
        alias.symlink_to(target, target_is_directory=True)
        destination = alias / "leaderboard"
    result = run("--apply", "--source-dir", source, "--dest-dir", destination)
    assert result.returncode != 0
    assert (target / "index.html").read_text() == "UNCHANGED"
    assert not (target / "leaderboard").exists()


def test_source_cannot_be_its_own_destination(source):
    before = (source / "index.html").read_bytes()
    assert run("--apply", "--source-dir", source, "--dest-dir", source).returncode != 0
    assert (source / "index.html").read_bytes() == before


def test_destination_file_directory_is_rejected_before_other_file_changes(tmp_path, source):
    destination = tmp_path / "pages"
    (destination / "data.json").mkdir(parents=True)
    (destination / "index.html").write_text("PREVIOUS_PAGE")
    assert run("--apply", "--source-dir", source, "--dest-dir", destination).returncode != 0
    assert (destination / "index.html").read_text() == "PREVIOUS_PAGE"


@pytest.mark.parametrize("args", [["--wat"], ["--source-dir"], ["--dest-dir", ""]])
def test_bad_arguments_fail_cleanly(args):
    result = run(*args)
    assert result.returncode != 0
    assert "leaderboard:" in result.stderr


def test_publisher_has_no_auto_export_network_git_or_scheduler_commands():
    script = SCRIPT.read_text()
    assert 'export-leaderboard.py" --validate' in script
    assert "--store" not in script
    for command in ("git push", "git commit", "curl ", "wget ", "crontab ", "launchctl ", "wrangler "):
        assert command not in script
    assert run("--help").returncode == 0
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0
