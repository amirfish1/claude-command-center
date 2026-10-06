"""Savings UI (L13): pure-helper contracts and wiring checks.

Extracts the SAVINGS_UI_START..END block from static/savings.js and executes
it in node, asserting the money/token formatters, the per-session chip model
("$0 · saved $X" for free runs, "$X value" otherwise), and the milestone
ladder. Also statically checks that index.html loads the module and that
app.js calls the row-chip hook.
"""
import json
import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]
SAVINGS_JS = ROOT / "static" / "savings.js"
INDEX_HTML = ROOT / "static" / "index.html"
APP_JS = ROOT / "static" / "app.js"


def _helpers_source():
    source = SAVINGS_JS.read_text(encoding="utf-8")
    start = source.index("// SAVINGS_UI_START")
    end = source.index("// SAVINGS_UI_END", start)
    return source[start:end]


def _run(expr):
    script = f"""
{_helpers_source()}
process.stdout.write(JSON.stringify({expr}));
"""
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True,
        capture_output=True, text=True,
    )
    return json.loads(completed.stdout)


def test_fmt_usd_rounds_and_groups():
    assert _run("[cccSavFmtUsd(0), cccSavFmtUsd(12.4), cccSavFmtUsd(0.004), "
                "cccSavFmtUsd(1234.56), cccSavFmtUsd(999.999), cccSavFmtUsd(-3), "
                "cccSavFmtUsd('abc')]") == [
        "$0.00", "$12.40", "$0.00", "$1,235", "$1,000", "$0.00", "$0.00"
    ]


def test_fmt_tokens_scales():
    assert _run("[cccSavFmtTokens(500), cccSavFmtTokens(2500), "
                "cccSavFmtTokens(3400000), cccSavFmtTokens(1200000000), "
                "cccSavFmtTokens(20000000000)]") == [
        "500", "3k", "3.4M", "1.2B", "20B"
    ]


def test_chip_free_run_with_known_value():
    chip = _run("cccSavChip({runtime:'free', cost_usd:1.82})")
    assert chip["cls"] == "is-free"
    assert chip["text"] == "$0 · saved $1.82"
    assert "free model" in chip["tip"]


def test_chip_free_run_without_value():
    chip = _run("cccSavChip({runtime:'free', cost_usd:null})")
    assert chip["cls"] == "is-free"
    assert chip["text"] == "$0"


def test_chip_paid_run_shows_api_value():
    chip = _run("cccSavChip({runtime:'api', cost_usd:0.47})")
    assert chip["cls"] == "is-api"
    assert chip["text"] == "$0.47 value"


def test_chip_hidden_when_no_cost():
    assert _run("[cccSavChip({cost_usd:null}), cccSavChip({cost_usd:0}), "
                "cccSavChip(null), cccSavChip({})]") == [None, None, None, None]


def test_milestones_reached_and_next():
    data = "{api_value_usd: 63.2, free_tokens: 500000000}"
    assert _run(f"cccSavReached({data}).map(m => m.id)") == ["work-10"]
    nxt = _run(f"cccSavNext({data})")
    assert nxt["milestone"]["id"] == "work-100"
    assert 0.6 < nxt["pct"] < 0.65


def test_milestones_all_reached_returns_no_next():
    data = "{api_value_usd: 5000, free_tokens: 2000000000}"
    assert _run(f"cccSavNext({data})") is None
    assert len(_run(f"cccSavReached({data})")) == 4


def test_ticker_model_carries_free_savings():
    model = _run("cccSavTicker({api_value_usd:12.4, free_saved_usd:3.1}, 'today')")
    assert model["text"] == "$12.40"
    assert model["free"] == "$3.10"
    assert "free models" in model["tip"]


def test_subline_prefers_free_story_then_roi():
    assert _run("cccSavSubline({free_saved_usd:3.1, free_runs:2})").startswith("$3.10")
    assert _run("cccSavSubline({free_saved_usd:0, roi_x:62.4, plan_cost_usd:200})") == (
        "That is a 62x return on your plan.")
    assert _run("cccSavSubline({free_saved_usd:0, roi_x:0, plan_cost_usd:0})") == ""


def test_index_html_loads_savings_assets():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert '<script src="/static/savings.js"></script>' in html
    assert 'href="/static/savings.css"' in html
    # savings.js must load before app.js so window.cccSavings exists for the
    # first row render.
    assert html.index("/static/savings.js") < html.index("/static/app.js")


def test_app_js_calls_row_chip_hook():
    src = APP_JS.read_text(encoding="utf-8")
    assert "window.cccSavings" in src
    assert "rowChipHtml(c)" in src
