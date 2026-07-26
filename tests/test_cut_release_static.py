from pathlib import Path


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
