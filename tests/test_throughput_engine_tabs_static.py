import pathlib


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_throughput_page_exposes_claude_codex_tabs_and_passes_engine_param():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert 'id="throughput-engine-tabs"' in throughput_html
    assert "setThroughputEngine('claude')" in throughput_html
    assert "setThroughputEngine('codex')" in throughput_html
    assert "setThroughputEngine('kimi')" in throughput_html
    assert 'id="engine-tab-kimi"' in throughput_html
    assert "activeThroughputEngine = 'claude'" in throughput_html
    assert "throughputEngineParam()" in throughput_html
    assert "engine=${encodeURIComponent(activeThroughputEngine)}" in throughput_html
    assert "activeThroughputEngine === 'claude'" in throughput_html
    assert "activeThroughputEngine === 'kimi'" in throughput_html


def test_server_throughput_aggregate_cache_and_payload_are_engine_aware():
    server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")

    assert "def _throughput_engine_filter(value):" in server_py
    assert "def _throughput_aggregate_cache_key(session_id, engine_filter=None):" in server_py
    assert "_throughput_aggregate_cache_key(session_id, engine_filter)" in server_py
    assert "engine_filter=engine_filter" in server_py
    assert "\"engine\": engine_filter or \"claude\"" in server_py
    assert "if engine_filter == \"codex\":" in server_py
    assert 'elif engine_filter == "kimi":' in server_py
    assert "def _throughput_kimi_turns_from_file(" in server_py
    assert "def _throughput_kimi_turns_from_wire(" in server_py
    assert 'v if v in ("codex", "kimi") else "claude"' in server_py
