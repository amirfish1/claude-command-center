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
