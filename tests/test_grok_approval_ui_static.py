"""Static regression coverage for actionable Grok ACP approvals."""

from pathlib import Path


APP_JS = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(
    encoding="utf-8",
)
SERVER_PY = (Path(__file__).resolve().parents[1] / "server.py").read_text(
    encoding="utf-8",
)


def test_grok_is_not_classified_as_a_claude_session():
    declaration = APP_JS.split("const NON_CLAUDE_SOURCES", 1)[1].split(";", 1)[0]
    assert "'grok'" in declaration


def test_live_approval_strip_renders_acp_permission_options():
    approval_branch = APP_JS.split("if (needsApproval) {", 1)[1].split(
        "if (isGenerating)", 1,
    )[0]
    assert "liveStatus.acpPendingPermission" in approval_branch
    assert "data-acp-harness" in approval_branch
    assert "data-acp-req" in approval_branch
    assert "data-acp-opt" in approval_branch


def test_session_status_preserves_acp_approval_state():
    session_status = SERVER_PY.split('elif path == "/api/session-status":', 1)[1].split(
        'elif re.match(r"^/api/conversations/', 1,
    )[0]
    assert "is_grok_status = _is_grok_session(sid)" in session_status
    assert "is_acp_status = is_kimi_status or is_grok_status" in session_status
    assert "if not is_acp_status:" in session_status
