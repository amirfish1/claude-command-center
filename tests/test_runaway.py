"""Runaway-session detector (ccc_server/runaway.py).

Synthetic transcripts in the shapes the real ones use: Claude `turnOrigin`
(human / scheduled / peer with isMeta), split assistant records that all carry
end_turn, queue-operation prompts; Codex task_started.turn_attribution and
token_count totals.
"""
import json
import os
import time
from datetime import datetime, timezone

import pytest

from ccc_server import runaway

NOW = 1_800_000_000.0
BIG = {"input_tokens": 10, "cache_read_input_tokens": 2_000_000, "cache_creation_input_tokens": 0,
       "output_tokens": 20_000}


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def claude_turn(origin, text, ts, mid, tools=(), meta=False):
    user = {"type": "user", "timestamp": iso(ts), "cwd": "/repo", "turnOrigin": origin,
            "message": {"role": "user", "content": text}}
    if meta:
        user["isMeta"] = True
    recs = [user]
    content = [{"type": "tool_use", "name": n, "input": i} for n, i in tools]
    if content:
        recs.append({"type": "assistant", "timestamp": iso(ts + 5),
                     "message": {"id": mid + "t", "stop_reason": "tool_use", "usage": BIG, "content": content}})
    # Real transcripts split one message into several records, each end_turn.
    for kind in ("thinking", "text"):
        recs.append({"type": "assistant", "timestamp": iso(ts + 10),
                     "message": {"id": mid, "stop_reason": "end_turn", "usage": BIG,
                                 "content": [{"type": kind, kind: "nothing new"}]}})
    return recs


def write(path, recs):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        for rec in recs:
            fh.write(json.dumps(rec) + "\n")
    os.utime(path, (NOW - 60, NOW - 60))


@pytest.fixture
def env(tmp_path):
    projects = tmp_path / "projects"
    codex = tmp_path / "codex"
    codex.mkdir()
    kw = dict(projects_root=projects, codex_root=codex, state_file=tmp_path / "scan.json")
    return tmp_path, projects, codex, kw


def timer_loop(start, n, tools=()):
    recs = []
    for i in range(n):
        recs += claude_turn("scheduled", "check the deploy", start + i * 1800, "m%d" % i,
                            tools=tools)
    return recs


def test_stagnant_timer_loop_is_flagged(env):
    _, projects, _, kw = env
    path = projects / "p" / "aaaaaaaa-1111.jsonl"
    write(path, claude_turn("human", "watch the deploy", NOW - 6 * 3600, "h0")
          + timer_loop(NOW - 5 * 3600, 8, tools=[("ScheduleWakeup", {"noop": True})]))
    flagged, _ = runaway.scan(now=NOW, **kw)
    assert len(flagged) == 1
    e = flagged[0]
    assert e["session_id"] == "aaaaaaaa-1111"
    assert e["auto_turns"] == 8  # split end_turn records did not double-count
    assert e["triggers"] == {"timer": 8}
    assert e["noop_turns"] == 8 and e["stagnant_turns"] == 8
    assert e["reasons"] == ["stagnant"]
    assert e["title"] == "watch the deploy"


def test_productive_loop_is_not_flagged(env):
    _, projects, _, kw = env
    write(projects / "p" / "bbbbbbbb-2222.jsonl",
          timer_loop(NOW - 5 * 3600, 8, tools=[("Bash", {"command": "git commit -m x && git push"})]))
    flagged, _ = runaway.scan(now=NOW, **kw)
    assert flagged == []


def test_human_turn_resets_streak(env):
    _, projects, _, kw = env
    path = projects / "p" / "cccccccc-3333.jsonl"
    write(path, timer_loop(NOW - 8 * 3600, 8)
          + claude_turn("human", "ok carry on", NOW - 1200, "h1"))
    flagged, _ = runaway.scan(now=NOW, **kw)
    assert flagged == []


def test_peer_meta_pings_and_queue_prompts_count(env):
    _, projects, _, kw = env
    recs = []
    for i in range(4):
        recs += claude_turn("peer", 'Another Claude session sent a message:\n<cross-session-message '
                            'from-name="wt:notify">ping', NOW - 5 * 3600 + i * 1800, "p%d" % i, meta=True)
    for i in range(3):
        ts = NOW - 3 * 3600 + i * 1800
        recs.append({"type": "queue-operation", "operation": "enqueue", "timestamp": iso(ts),
                     "content": "<cross-session-message from-name=\"orchestrator\">status?"})
        recs.append({"type": "queue-operation", "operation": "dequeue", "timestamp": iso(ts)})
        recs.append({"type": "assistant", "timestamp": iso(ts + 9),
                     "message": {"id": "q%d" % i, "stop_reason": "end_turn", "usage": BIG,
                                 "content": [{"type": "text", "text": "ack"}]}})
    write(projects / "p" / "dddddddd-4444.jsonl", recs)
    flagged, _ = runaway.scan(now=NOW, **kw)
    assert len(flagged) == 1
    assert flagged[0]["triggers"] == {"watchtower": 4, "peer": 3}


def test_repeated_human_answer_relay_is_automation():
    st = runaway._new_state()
    text = "[sender: watchtower (service) · msg x]\nA human answered your blocked question on ticket OPS-7."
    assert runaway._resolve_answer(st, runaway.text_trigger(text), text) == runaway.HUMAN
    assert runaway._resolve_answer(st, runaway.text_trigger(text), text) == "watchtower"


def test_text_trigger_classes():
    assert runaway.text_trigger("please fix the bug") == runaway.HUMAN
    assert runaway.text_trigger("[WATCHTOWER] A new comment") == "watchtower"
    assert runaway.text_trigger("[watchtower] WT-24 claimed") == "watchtower"
    assert runaway.text_trigger("[sender: wt:notify (service) · msg 1]\nx") == "watchtower"
    assert runaway.text_trigger("[sender: 01a18042 (session) · msg 1]\nx") == "peer"
    assert runaway.text_trigger("[sender: amir (human) · msg 1]\nx") == runaway.HUMAN


def codex_rollout(codex, sid, turns, trigger="goal"):
    day = codex / time.strftime("%Y/%m/%d", time.localtime(NOW - 6 * 3600))
    path = day / ("rollout-2026-10-29T00-00-00-%s.jsonl" % sid)
    recs = [{"type": "session_meta", "timestamp": iso(NOW - 6 * 3600), "payload": {"id": sid, "cwd": "/repo"}}]
    total = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    recs.append({"type": "event_msg", "timestamp": iso(NOW - 6 * 3600),
                 "payload": {"type": "token_count", "info": {"total_token_usage": dict(total)}}})
    for i in range(turns):
        ts = NOW - 5 * 3600 + i * 1800
        recs.append({"type": "event_msg", "timestamp": iso(ts), "payload": {
            "type": "task_started", "turn_attribution": {"turn_trigger": trigger}}})
        recs.append({"type": "response_item", "timestamp": iso(ts), "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text":
                '<codex_internal_context source="goal">\nContinue working toward the active goal'}]}})
        recs.append({"type": "response_item", "timestamp": iso(ts + 5), "payload": {
            "type": "function_call", "name": "exec_command", "arguments": json.dumps({"cmd": "git status"})}})
        total = {"input_tokens": total["input_tokens"] + 2_100_000,
                 "cached_input_tokens": total["cached_input_tokens"] + 2_000_000,
                 "output_tokens": total["output_tokens"] + 10_000}
        recs.append({"type": "event_msg", "timestamp": iso(ts + 6),
                     "payload": {"type": "token_count", "info": {"total_token_usage": dict(total)}}})
        recs.append({"type": "event_msg", "timestamp": iso(ts + 9), "payload": {"type": "task_complete"}})
    write(path, recs)
    return path


def test_codex_goal_loop_is_flagged_and_incremental(env):
    _, _, codex, kw = env
    sid = "01a18617-8314-7c32-8b0b-ab2ee8d53211"
    path = codex_rollout(codex, sid, 8)
    flagged, _ = runaway.scan(now=NOW, **kw)
    assert [e["session_id"] for e in flagged] == [sid]
    e = flagged[0]
    assert e["engine"] == "codex" and e["triggers"] == {"goal": 8}
    # per turn: 100k fresh + 0.1*2M cached + 5*10k output = 350k
    assert e["cost_tokens"] == 8 * 350_000
    # A human message appended later resets the streak (only new bytes read).
    write(path, [{"type": "event_msg", "timestamp": iso(NOW - 30), "payload": {"type": "task_started",
                  "turn_attribution": {"turn_trigger": None}}},
                 {"type": "event_msg", "timestamp": iso(NOW - 30), "payload": {
                     "type": "user_message", "message": "stop, we're done"}}])
    flagged, _ = runaway.scan(now=NOW, **kw)
    assert flagged == []


def test_lease_hides_session(env, monkeypatch):
    tmp, projects, _, kw = env
    write(projects / "p" / "eeeeeeee-5555.jsonl",
          timer_loop(NOW - 5 * 3600, 8, tools=[("ScheduleWakeup", {"noop": True})]))
    leases = tmp / "leases.json"
    assert len(runaway.state(now=NOW, leases_file=leases, **kw)["sessions"]) == 1
    runaway.set_lease("eeeeeeee-5555", 24, now=NOW, leases_file=leases)
    out = runaway.state(now=NOW, leases_file=leases, **kw)
    assert out["sessions"] == [] and out["leased"] == ["eeeeeeee-5555"]
    later = runaway.state(now=NOW + 25 * 3600, leases_file=leases, **kw)
    assert later["leased"] == []
