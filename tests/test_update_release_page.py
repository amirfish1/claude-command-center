import importlib.util
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "update_release_page", Path(__file__).resolve().parents[1] / "scripts/update-release-page.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_release_labels_and_download_follow_release_but_capture_stays_honest():
    page = '''<span class="version">v5.39</span>
    <span>v5.39.0</span><p>Since v5.33</p>
    <a href="releases/download/v5.39.0/ccc.dmg">Download</a>
    <img src="./images/ccc-v5-39-hero.png" alt="CCC v5.39 showing the fleet">'''
    updated = MODULE.update_page(page, "5.41.0", "5.39.0")
    assert '<span class="version">v5.41</span>' in updated
    assert '<span>v5.41.0</span>' in updated
    assert 'releases/download/v5.39.0/ccc-v5.39.0.dmg' in updated
    assert '<p>Since v5.33</p>' in updated
    assert '<img src="./images/ccc-v5-39-hero.png" alt="CCC v5.39 showing the fleet">' in updated
    assert MODULE.update_page(updated, "5.41.1", "5.39.0").count("v5.41.1") == 1
    published = MODULE.update_page(updated, "5.41.0", "5.41.0")
    assert 'releases/download/v5.41.0/ccc-v5.41.0.dmg' in published
