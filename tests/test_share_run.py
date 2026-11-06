"""Local "share this run" export and stats card."""
import ast
import json
import re
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ccc_server import share_redact, share_run, usage_stats

ROOT = Path(__file__).resolve().parents[1]
SID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
FAKE_TOKEN = "ghp_" + "X" * 36
IDENTITY = {"people": ["Dana Okafor", "dokafor"], "projects": ["acme-billing"]}


def session_events():
    return [
        {"type": "user_text", "ts": "2026-11-05T10:00:00Z",
         "text": f"Fix the refund bug for Dana Okafor in /home/dokafor/acme-billing/app.py {FAKE_TOKEN}"},
        {"type": "user_text", "ts": "2026-11-05T10:00:01Z", "bookkeeping": True,
         "text": "bookkeeping note that must vanish"},
        {"type": "assistant", "ts": "2026-11-05T10:00:05Z", "model": "claude-opus-5-5", "blocks": [
            {"kind": "thinking", "text": "private reasoning about Dana"},
            {"kind": "text", "text": "I'll look at the handler first."},
            {"kind": "tool_use", "name": "Bash", "id": "t1",
             "command": "cd /home/dokafor/acme-billing && pytest tests/test_refund.py --token=abc",
             "detail": "pytest tests/test_refund.py"},
            {"kind": "tool_use", "name": "Edit", "id": "t2", "detail": "/home/dokafor/acme-billing/app.py"},
            {"kind": "tool_use", "name": "mcp__acmecorp__lookup", "id": "t3", "detail": "customer 991"},
            {"kind": "tool_use", "name": "Bash", "id": "t4",
             "command": "git commit -m 'fix refund for Dana'", "detail": "git commit"},
        ]},
        {"type": "tool_result", "ts": "2026-11-05T10:00:09Z", "tool_use_id": "t1", "is_error": True,
         "text": f"FAILED for dana@example.com at 10.1.2.3 {FAKE_TOKEN}"},
        {"type": "assistant", "ts": "2026-11-05T10:07:05Z", "model": "claude-opus-5-5", "is_sidechain": True,
         "blocks": [{"kind": "text", "text": "sidechain chatter"}]},
        {"type": "assistant", "ts": "2026-11-05T10:08:05Z", "model": "private-finetune-acme", "blocks": [
            {"kind": "text", "text": "Fixed. The refund now rounds correctly."}]},
    ]


def export(events=None, **kwargs):
    return share_run.build_replay(
        session_events() if events is None else events,
        engine="claude", identity=IDENTITY, **kwargs,
    )[0]


def everything(replay):
    return "\n".join((json.dumps(replay), share_run.render_html(replay), share_run.render_svg(replay)))


@pytest.mark.parametrize("leak", [
    "Dana", "Okafor", "dokafor", "acme", "/home", "app.py", FAKE_TOKEN[:12], "dana@", "10.1.2.3",
    "private reasoning", "FAILED", "bookkeeping", "sidechain", "--token", "test_refund",
    "mcp__", "customer 991", "2026", "10:00", "private-finetune", SID,
])
def test_export_never_carries_sensitive_material(leak):
    assert leak not in everything(export())


def test_export_keeps_the_story():
    replay = export()
    assert [s["kind"] for s in replay["steps"]] == ["user", "agent", "tools", "agent"]
    assert "Fix the refund bug" in replay["steps"][0]["text"]
    assert replay["steps"][1]["text"] == "I'll look at the handler first."
    tools = {t["label"]: t["count"] for t in replay["steps"][2]["tools"]}
    assert tools == {"Bash pytest": 1, "Edit": 1, "Tool": 1, "Bash git commit": 1}
    assert replay["steps"][2]["errors"] == 1
    assert replay["stats"] == {
        "duration_sec": 485, "prompts": 1, "replies": 2, "tool_calls": 4, "tool_errors": 1,
        "files_edited": 1, "commits": 1, "test_runs": 1,
    }
    assert replay["model"] == "claude-opus-5-5"
    assert replay["engine"] == "Claude Code"


def test_unknown_engine_is_not_echoed():
    replay = share_run.build_replay(session_events(), engine="acme-agent", identity=IDENTITY)[0]
    assert replay["engine"] == "Coding agent"


@pytest.mark.parametrize("command,verb", [
    ("git push origin main", "git push"),
    ("cd /tmp && npm test", "npm"),
    ("FOO=1 nice -n 15 python3 -m pytest", "python3"),
    ("/opt/acme/bin/deploy --now", ""),
    ("./acme-tool && rm -rf /", ""),
    ("/usr/bin/git -C /x status", "git status"),
])
def test_command_label_is_allow_listed(command, verb):
    assert share_run._command_verb(command) == verb


def test_title_override_is_redacted_too():
    replay = export(title="Refund fix for Dana Okafor")
    assert "Dana" not in replay["title"] and replay["title"].startswith("Refund fix")


def test_hide_and_user_hide_file(tmp_path, monkeypatch):
    hide_file = tmp_path / "share-hide.txt"
    hide_file.write_text("# comment\nhandler\n")
    monkeypatch.setattr(share_run, "_hide_file", lambda: hide_file)
    text = everything(export(hide=["rounds"]))
    assert "handler" not in text and "rounds" not in text and "comment" not in text


def test_long_session_is_capped():
    events = []
    for index in range(1000):
        events.append({"type": "user_text", "ts": "2026-11-05T10:00:00Z", "text": f"step {index}"})
    replay = export(events)
    assert len(replay["steps"]) == share_run.MAX_STEPS + 1
    assert any(s["kind"] == "gap" and s["count"] == 760 for s in replay["steps"])


def test_page_is_inert_and_self_contained():
    page = share_run.render_html(export())
    assert 'http-equiv="Content-Security-Policy"' in page
    assert "default-src 'none'" in page
    assert "connect-src" not in page
    assert not re.search(r"<(?:img|iframe|link|object|embed)\b", page)
    assert not re.search(r"<script[^>]*\bsrc=", page)
    assert not re.search(r"\b(?:fetch|XMLHttpRequest|WebSocket|sendBeacon|EventSource)\b", page)
    assert set(re.findall(r"https?://[^\s\"'<>]+", page)) == {share_run.REPO_URL}


def test_page_escapes_html_from_the_transcript():
    events = [{"type": "user_text", "ts": "2026-11-05T10:00:00Z",
               "text": "<img src=x onerror=alert(1)> <script>alert(1)</script> fix"}]
    page = share_run.render_html(export(events))
    body = page.split("<body>", 1)[1].split("<script>", 1)[0]
    assert "<img" not in body and "<script" not in body
    svg = share_run.render_svg(export(events))
    assert "<script" not in svg and "<img" not in svg


def test_export_refuses_when_verification_fails(monkeypatch):
    def leaky(self, value, limit=0):
        return "leaked /home/dokafor/plan.txt"

    monkeypatch.setattr(share_redact.Redactor, "text", leaky)
    payload, status = share_run.export_conversation(
        SID, parse=lambda _sid: {"events": session_events()}, transcript_path="",
    )
    assert status == 422
    assert payload["ok"] is False
    assert "html" not in payload and "svg" not in payload and "replay" not in payload
    assert "dokafor" not in json.dumps(payload)


def test_export_of_empty_session_is_a_clear_error():
    payload, status = share_run.export_conversation(
        SID, parse=lambda _sid: {"events": []}, transcript_path="",
    )
    assert status == 404 and not payload["ok"]


def test_identity_covers_git_and_session_cwd(tmp_path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("git not installed")
    repo = tmp_path / "zorblax-repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    run("init", "-q")
    run("config", "user.name", "Dana Okafor")
    run("config", "user.email", "dokafor@example.com")
    run("remote", "add", "origin", "git@example.com:acmeorg/billing-core.git")
    share_run._identity_cache.clear()
    identity = share_run.machine_identity([str(repo)])
    assert "Dana Okafor" in identity["people"]
    assert "dokafor" in identity["people"]
    assert "acmeorg" in identity["people"]
    assert "billing-core" in identity["projects"]
    assert "zorblax-repo" in identity["projects"]


def test_cwd_is_read_from_the_transcript_head(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"cwd":"/work/zorblax"}\n{"payload":{"cwd": "/work/other"}}\n')
    assert share_run.transcript_cwds(transcript) == ["/work/other", "/work/zorblax"]
    assert share_run.transcript_cwds(tmp_path / "missing") == []


def test_list_params_are_bounded():
    assert share_run.parse_list("a, b\nc,,") == ["a", "b", "c"]
    assert len(share_run.parse_list(",".join(["x"] * 500))) == share_run.MAX_LIST_ITEMS
    assert len(share_run.parse_list("y" * 500)[0]) == share_run.MAX_LIST_ITEM_CHARS


def throughput_source(agents=12):
    return lambda: ({
        "ok": True, "pending": False, "agents": {"7": agents, "30": 40},
        "daily": [
            {"day": "2026-10-01", "turns": 900, "tokens": 900},
            {"day": "2026-11-04", "turns": 30, "tokens": 300},
            {"day": "2026-11-05", "turns": 12, "tokens": 120},
        ],
    }, 200)


def productivity_source(rows):
    return lambda: ({"daily": rows} if rows is not None else {"state": "building"}, 200)


NOV_5 = 1793923200  # 2026-11-05, noon-ish in most zones


@pytest.fixture(autouse=True)
def fresh_caches():
    share_run._stats_cache.clear()
    share_run._identity_cache.clear()


def test_stats_card_is_numbers_only():
    payload, status = share_run.stats_card(
        throughput=throughput_source(),
        productivity=productivity_source([
            {"date": "2026-10-01", "commits": 500, "title": "private"},
            {"date": "2026-11-04", "commits": 300},
            {"date": "2026-11-05", "commits": 40},
        ]),
        now=NOV_5,
    )
    assert status == 200
    assert (payload["agents"], payload["commits"], payload["turns"]) == (12, 340, 42)
    assert "12 agents, 340 commits this week" in payload["svg"]
    assert "private" not in json.dumps(payload)
    assert set(payload) == {"ok", "pending", "days", "agents", "commits", "turns", "tokens", "svg"}


def test_stats_card_leaves_out_commits_it_does_not_know():
    payload, _ = share_run.stats_card(
        throughput=throughput_source(), productivity=productivity_source(None), now=NOV_5,
    )
    assert payload["commits"] is None
    assert "commits" not in payload["svg"]


def test_stats_card_waits_for_a_pending_build():
    payload, status = share_run.stats_card(
        throughput=lambda: ({"ok": True, "pending": True, "daily": []}, 200),
        productivity=productivity_source([]),
        now=NOV_5,
    )
    assert status == 200 and payload == {"ok": True, "pending": True}
    assert not share_run._stats_cache


def test_agents_are_distinct_sessions_and_ids_stay_out():
    now = 1793923200
    turn = lambda sid, age_days: {
        "session_id": sid, "tokens_in": 1, "tokens_out": 1,
        "t_end": datetime.fromtimestamp(now - age_days * 86400, timezone.utc).isoformat(),
    }
    payload = usage_stats._throughput_share_aggregate(
        {"claude": [turn("s1", 1), turn("s1", 2), turn("s2", 3), turn("s3", 20)],
         "codex": [turn("s4", 0), turn("s5", 200)]},
        now=now,
    )
    assert payload["agents"] == {"7": 3, "30": 4}
    assert "s1" not in json.dumps(payload)


@pytest.mark.parametrize("module", ["ccc_server/share_redact.py", "ccc_server/share_run.py"])
def test_share_code_cannot_reach_the_network_or_telemetry(module):
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {node.module or ""} | {f"{node.module}.{a.name}" for a in node.names}
    forbidden = ("urllib", "http", "requests", "ftplib", "smtplib", "ssl", "asyncio", "websocket")
    assert not [name for name in imported if name.split(".")[0] in forbidden]
    assert not [name for name in imported if "telemetry" in name]


def test_export_opens_no_socket(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("share export tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    payload, status = share_run.export_conversation(
        SID, parse=lambda _sid: {"events": session_events()}, transcript_path="",
    )
    assert status == 200 and payload["ok"]


def test_ui_only_talks_to_its_own_dashboard():
    source = (ROOT / "static" / "share-run.js").read_text(encoding="utf-8")
    assert set(re.findall(r"fetch\((['\"][^'\"]*['\"])", source)) == {
        "'/api/conversations/'", "'/api/share/stats?days='",
    }
    assert not re.search(r"sendBeacon|XMLHttpRequest|WebSocket|/api/throughput/share|workers\.dev", source)


def test_nudge_harness():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    result = subprocess.run(
        [node, "tests/share_run_nudge_harness.cjs"], cwd=ROOT,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
