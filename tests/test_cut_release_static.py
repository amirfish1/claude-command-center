import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap


ROOT = Path(__file__).resolve().parents[1]


def test_full_release_validates_notary_profile_before_mutating_release_state():
    script = (ROOT / "scripts" / "cut-release.sh").read_text(encoding="utf-8")

    profile_check = "notarytool history --keychain-profile ccc-notary"
    first_mutation = "python3 scripts/release.py ${VERSION}"

    assert profile_check in script
    assert script.index(profile_check) < script.index(first_mutation)
    assert 'scripts/macapp/vendor/bin/sign_update' in script
    assert "notarization profile 'ccc-notary' is unavailable" in script


def test_homebrew_publish_syncs_and_verifies_the_remote_formula():
    script = (ROOT / "scripts" / "cut-release.sh").read_text(encoding="utf-8")

    rebase = 'git -C "${BREW_TAP}" pull --rebase origin main'
    formula_update = 'sed -i \'\' -E "s#archive/refs/tags/v[0-9.]+\\.tar\\.gz#archive/refs/tags/v${VERSION}.tar.gz#"'

    assert rebase in script
    assert script.index(rebase) < script.index(formula_update)
    assert 'failed to publish Homebrew formula' in script
    assert 'git -C "${BREW_TAP}" fetch origin main' in script
    assert 'git -C "${BREW_TAP}" show FETCH_HEAD:Formula/ccc.rb' in script
    assert 'if [ "$DRY_RUN" = 0 ]; then\n      step "     syncing Homebrew tap"\n      git -C "${BREW_TAP}" pull --rebase origin main' in script


def _run_release_with_formula_outcome(*, push_status, published_formula):
    with tempfile.TemporaryDirectory() as temp_dir:
        workspace = Path(temp_dir)
        repo = workspace / "repo"
        tap = workspace / "tap"
        bin_dir = workspace / "bin"
        (repo / "scripts").mkdir(parents=True)
        (tap / "Formula").mkdir(parents=True)
        bin_dir.mkdir()

        script = repo / "scripts" / "cut-release.sh"
        shutil.copy2(ROOT / "scripts" / "cut-release.sh", script)
        (repo / "pyproject.toml").write_text('version = "0.0.0"\n', encoding="utf-8")
        (repo / "server.py").write_text('__version__ = "0.0.0"\n', encoding="utf-8")
        (repo / "CHANGELOG.md").write_text("[Unreleased]: test\n", encoding="utf-8")
        (repo / "changelog.d").mkdir()
        (tap / "Formula" / "ccc.rb").write_text(
            'url "https://github.com/amirfish1/claude-command-center/archive/refs/tags/v0.0.0.tar.gz"\n'
            'sha256 "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n',
            encoding="utf-8",
        )
        published = workspace / "published.rb"
        published.write_text(published_formula, encoding="utf-8")

        def executable(name, content):
            executable_path = bin_dir / name
            executable_path.write_text(textwrap.dedent(content), encoding="utf-8")
            executable_path.chmod(0o755)

        executable(
            "git",
            """\
            #!/usr/bin/env bash
            if [ "$1" = "-C" ]; then
              case "$3" in
                pull|fetch) exit 0 ;;
                show) cat "$FAKE_PUBLISHED_FORMULA"; exit 0 ;;
              esac
            fi
            if [ "$PWD" = "$FAKE_BREW_TAP" ] && [ "$1" = "push" ]; then
              echo "formula push rejected" >&2
              exit "$FAKE_BREW_PUSH_STATUS"
            fi
            if [ "$1" = "describe" ]; then echo v0.0.0; fi
            if [ "$1" = "rev-parse" ]; then exit 1; fi
            exit 0
            """,
        )
        executable("gh", "#!/usr/bin/env bash\nexit 0\n")
        executable("curl", "#!/usr/bin/env bash\nprintf 'release fixture\\n'\n")
        executable("python3", "#!/usr/bin/env bash\nexit 0\n")

        env = os.environ | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CCC_BREW_TAP": str(tap),
            "FAKE_BREW_TAP": str(tap),
            "FAKE_BREW_PUSH_STATUS": str(push_status),
            "FAKE_PUBLISHED_FORMULA": str(published),
        }
        return subprocess.run(
            [str(script), "9.9.9", "--skip-dmg"],
            cwd=repo,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )


def test_homebrew_push_rejection_aborts_before_release_success_banner():
    result = _run_release_with_formula_outcome(push_status=1, published_formula="")

    assert result.returncode != 0
    assert "failed to publish Homebrew formula" in result.stderr
    assert "Done — v9.9.9 shipped" not in result.stdout


def test_remote_formula_mismatch_aborts_before_release_success_banner():
    result = _run_release_with_formula_outcome(
        push_status=0,
        published_formula='url "https://example.invalid/not-v9.9.9.tar.gz"\nsha256 "wrong"\n',
    )

    assert result.returncode != 0
    assert "published Homebrew formula does not match v9.9.9" in result.stderr
    assert "Done — v9.9.9 shipped" not in result.stdout
