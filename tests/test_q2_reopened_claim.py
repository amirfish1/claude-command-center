"""Regression coverage for reopened tickets in the standalone queue board."""

import pathlib


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_reopened_ticket_resume_handle_does_not_count_as_a_live_claim():
    q2_js = (PROJECT_ROOT / "static" / "q2.js").read_text(encoding="utf-8")
    start = q2_js.index("function hasLiveClaim(it)")
    end = q2_js.index("\n  // A claim whose worker is gone.", start)
    claim_fn = q2_js[start:end]

    assert "var hasClaimant = !!(it && (it.claimed_by || it.claimed_at));" in claim_fn
    assert "if (!hasClaimant) return false;" in claim_fn
