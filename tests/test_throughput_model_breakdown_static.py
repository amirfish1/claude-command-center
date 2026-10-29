import pathlib


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_model_breakdown_labels_selected_window_share():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "function formatModelWeeklyPct" in throughput_html
    assert "const modelRows = summary.per_model || []" in throughput_html
    assert "renderModelBreakdown(modelRows, modelBreakdownContext(modelRows))" in throughput_html
    assert "windowAdjustedTokens" in throughput_html
    assert 'title="Cache-adjusted tokens in the selected activity window">Window share' in throughput_html
    assert "token shares do not measure subscription quota attribution" in throughput_html
    assert "model-weekly-cell" in throughput_html


def test_model_share_does_not_allocate_account_quota():
    import json
    import subprocess

    html = (PROJECT_ROOT / "static/throughput.html").read_text()
    start = html.index("    function formatModelWeeklyPct(")
    end = html.index("    function renderModelBreakdown", start)
    script = html[start:end] + """
console.log(JSON.stringify([
  formatModelWeeklyPct({effective_input_tokens: 200, output_tokens: 50},
    {windowAdjustedTokens: 1000, codexWeeklyPct: 85}),
  formatModelWeeklyPct({effective_input_tokens: 200}, {}),
]));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == ["25.00%", "-"]


def test_session_cost_uses_selected_window_and_preserves_unpriced_warning():
    import json
    import subprocess

    html = (PROJECT_ROOT / "static/throughput.html").read_text()
    start = html.index("    function windowSessionCost(")
    end = html.index("    function renderTopSessions", start)
    script = "const fmtCost = n => '$' + n.toFixed(2);\n" + html[start:end] + """
console.log(JSON.stringify([
  windowSessionCost({_week_cost: 150, _cost_usd: 999}),
  windowSessionCost({_week_cost: 12, _week_unpriced: 3}),
  windowSessionCost({_week_unpriced: 3}),
]));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == ["$150.00", "$12.00 + unpriced", "n/a"]


def test_continuation_names_use_task_and_distinguishing_identifier():
    import json
    import subprocess

    html = (PROJECT_ROOT / "static/throughput.html").read_text()
    start = html.index("    function throughputSessionName(")
    end = html.index("    function renderTopSessions", start)
    script = html[start:end] + r"""
console.log(JSON.stringify([
  throughputSessionName({session_id: 'example', display_name: 'You continue the work of session previous',
    _window_details: {name: 'You continue the work of session previous\\n\\nTask: Complete preserved branches.\\nDetails'}}),
  throughputSessionName({session_id: 'example', display_name: 'You continue the work of session previous'}),
  throughputSessionName({session_id: 'example', display_name: 'Release coordinator'}),
]));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == ["Continuation: Complete preserved branches.", "Continuation", "Release coordinator"]
    assert "escapeThroughputText(s.session_id.substring(0, 13))" in html


def test_late_aggregate_response_cannot_replace_new_window():
    import json
    import subprocess

    html = (PROJECT_ROOT / "static/throughput.html").read_text()
    start = html.index("    async function selectAggregateSession(")
    end = html.index("    async function selectSession", start)
    script = """
let throughputSelectionVersion = 0, activeThroughputEngine = 'codex', activeThroughputRange = '7d', activeSessionId;
const THROUGHPUT_RANGE_LABELS = {'7d': 'Last 7 Days'};
let completeResponse, rendered = 0, hidden = 0;
const renderSessionList = () => {}, updateActiveEngineBadge = () => {}, setStatus = () => {}, showLoader = () => {};
const aggregateThroughputUrl = () => '/test';
const fetch = () => new Promise(resolve => { completeResponse = () => resolve({ok: true, json: async () => ({ok: true})}); });
const renderDashboard = () => rendered++;
const hideLoader = () => hidden++;
""" + html[start:end] + """
(async () => {
  const pending = selectAggregateSession({session_id: 'all_7_days'});
  activeThroughputRange = '1d';
  completeResponse();
  await pending;
  console.log(JSON.stringify({rendered, hidden}));
})();
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == {"rendered": 0, "hidden": 0}


def test_weekly_bootstrap_rejected_after_selecting_day_window():
    import json
    import subprocess

    html = (PROJECT_ROOT / "static/throughput.html").read_text()
    start = html.index("    function bootstrapSelectionMatches(")
    end = html.index("    function applyThroughputBootstrap", start)
    script = "let activeThroughputRange = '1d', activeThroughputEngine = 'codex', activeSessionId = 'all_7_days';\n" + html[start:end] + """
console.log(JSON.stringify(bootstrapSelectionMatches({session_id: 'all_7_days'}, 'codex')));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) is False
