import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
PAGES = {
    "captain": DOCS / "captain" / "index.html",
    "watchtower": DOCS / "watchtower" / "index.html",
    "receipts": DOCS / "receipts" / "index.html",
}


def _read(path):
    return path.read_text(encoding="utf-8")


def test_campaign_routes_and_shared_assets_exist():
    for route_path in PAGES.values():
        assert route_path.is_file(), route_path
    assert (DOCS / "agent-operations" / "site.css").is_file()
    assert (DOCS / "agent-operations" / "site.js").is_file()
    assert (DOCS / "captain" / "captain.js").is_file()


def test_captain_fixture_is_ordered_bounded_and_public_safe():
    events = json.loads(_read(DOCS / "captain" / "events.json"))["events"]
    assert 8 <= len(events) <= 12
    assert [event["at_ms"] for event in events] == sorted(
        event["at_ms"] for event in events
    )
    kinds = {event["kind"] for event in events}
    assert {
        "GOAL_RECEIVED",
        "PLAN_SHAPED",
        "JOB_FILED",
        "CONTEXT_REUSED",
        "WORKER_SPAWNED",
        "DEPENDENCY_WAIT",
        "VERIFICATION_STARTED",
        "JOB_RESOLVED",
        "RECEIPT_RETURNED",
    } <= kinds
    encoded = json.dumps(events)
    assert "/Users/" not in encoded
    assert "/home/" not in encoded
    assert "Outsourcerer" not in encoded


def test_captain_page_is_honest_and_accessible():
    page = _read(PAGES["captain"])
    assert "Give one agent a goal. WatchTower gives it a crew." in page
    assert "Recorded walkthrough" in page
    assert 'aria-label="Captain demo controls"' in page
    assert 'id="captainPlay"' in page
    assert 'id="captainReplay"' in page
    assert 'aria-live="polite"' in page
    assert 'href="/watchtower/"' in page
    assert 'href="/receipts/"' in page


def test_supporting_pages_have_distinct_promises_and_verified_commands():
    wt = _read(PAGES["watchtower"])
    receipts = _read(PAGES["receipts"])
    assert "A job system your coding agents can operate." in wt
    assert "wt import plan.md -q LAUNCH --apply" in wt
    assert "wt status -q LAUNCH" in wt
    assert "wt wait -q LAUNCH --timeout 1800" in wt
    assert 'href="https://github.com/amirfish1/watchtower"' in wt
    assert "An agent saying &ldquo;done&rdquo; is not a receipt." in receipts
    assert "commit or explicit no-code proof" in receipts


def test_every_campaign_page_has_canonical_social_and_shared_navigation():
    for route_name, route_path in PAGES.items():
        page = _read(route_path)
        assert f"https://ccc.amirfish.ai/{route_name}/" in page
        assert (
            'property="og:image" '
            'content="https://ccc.amirfish.ai/images/captain-social.png"'
        ) in page
        assert 'href="/agent-operations/site.css"' in page
        assert 'src="/agent-operations/site.js"' in page
        assert 'href="/captain/"' in page
        assert 'href="/watchtower/"' in page
        assert 'href="/receipts/"' in page


def test_homepage_current_facts_and_campaign_links():
    page = _read(DOCS / "index.html")
    assert "v5.19.1" in page
    assert "v5.8" not in page
    assert "seven engines" in page
    assert "Source-available" in page
    assert "Open source" not in page
    assert "open source" not in page
    assert "MIT &copy;" not in page
    assert 'href="/captain/"' in page
    assert 'href="/watchtower/"' in page
    assert 'href="/receipts/"' in page
