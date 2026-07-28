from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sidebar_rows_mark_sessions_without_transcript_content():
    """Only real conversation rows without a first transcript message get [EMPTY].

    spawn_recent was added to the condition once rows started reaching the list
    BEFORE their engine writes a transcript (measured: row at ~4.9s, Codex
    writes its first message at ~6.9s). A session that is still starting is not
    a created-but-unused one, so the chip waits out the spawn grace window.
    """
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    app_css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")

    assert (
        "const emptySessionChipHtml = (!isBacklogRow && !isGithubPrRow "
        "&& !c.first_message && !c.spawn_recent)"
    ) in app_js
    assert 'class="conv-empty-session-chip"' in app_js
    assert ">[EMPTY]</span>" in app_js
    assert "+ emptySessionChipHtml" in app_js
    assert ".conv-empty-session-chip" in app_css
