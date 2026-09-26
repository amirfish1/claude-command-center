from pathlib import Path


def test_right_rail_last_ask_does_not_rewrite_identical_markup():
    """The periodic rail coordinator must not repaint an unchanged Last ask."""
    app_js = (Path(__file__).parents[1] / "static" / "app.js").read_text()
    start = app_js.index("  function _railSeedLastAsk() {")
    end = app_js.index("  // Coordinator:", start)
    rail_seed = app_js[start:end]

    assert "if (st.earlierFirst.dataset.rawText === text) return;" in rail_seed
    assert "st.earlierFirst.dataset.rawText = text;" in rail_seed


def test_dynamic_earlier_ask_updates_the_same_text_cache():
    """Scroll-pinned asks must not leave a stale cache for the rail seeder."""
    app_js = (Path(__file__).parents[1] / "static" / "app.js").read_text()
    start = app_js.index("  function _dynAskApply(idx, items) {")
    end = app_js.index("  function _setEarlierAskLabel", start)
    dynamic_apply = app_js[start:end]

    assert "st.earlierFirst.dataset.rawText = text;" in dynamic_apply


def test_daily_checkin_chip_is_gated_on_server_feature_flag():
    src = (Path(__file__).parents[1] / "static" / "app.js").read_text(encoding="utf-8")
    chips = [line for line in src.splitlines() if "Daily check-in</button>" in line]
    assert chips, "check-in chip markup moved; update this test"
    assert all("askCheckinEnabled ?" in line for line in chips)
    assert "askCheckinEnabled = on" in src
