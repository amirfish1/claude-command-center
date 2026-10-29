"""Runaway-session detector (ccc_server/runaway.py).

Synthetic transcripts in the shapes the real ones use: Claude `turnOrigin`
(human / scheduled / peer with isMeta), split assistant records that all carry
end_turn, queue-operation prompts; Codex task_started.turn_attribution and
token_count totals; Kimi wire.jsonl loop events; Devin and Hermes SQLite rows.
"""
import json
import os
import sqlite3
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
    kw = dict(projects_root=projects, codex_root=codex, state_file=tmp_path / "scan.json",
              kimi_root=tmp_path / "kimi", devin_dbs=[], hermes_dbs=[])
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


def test_dashboard_state_never_scans_on_the_request_thread(monkeypatch):
    started = []
    monkeypatch.setattr(runaway, "_MEMO", {"at": 0.0, "result": None, "running": False})
    monkeypatch.setattr(runaway, "scan", lambda **kw: pytest.fail("scanned on the request thread"))

    class FakeThread:
        def __init__(self, target, **kw):
            self.target = target

        def start(self):
            started.append(self.target)

    monkeypatch.setattr(runaway.threading, "Thread", FakeThread)
    assert runaway.state()["sessions"] == []
    assert runaway.state()["sessions"] == []  # refresh already running: no second thread
    assert started == [runaway._refresh_memo]


# ------------------------------------------------------------ other engines

def kimi_turn(origin, text, ts, finish="end_turn", tool=None):
    ms = lambda t: int(t * 1000)
    recs = [{"type": "turn.prompt", "time": ms(ts), "input": [{"type": "text", "text": text}],
             "origin": origin}]
    recs.append({"type": "context.append_loop_event", "time": ms(ts + 1),
                 "event": {"type": "step.begin"}})
    if tool:
        recs.append({"type": "context.append_loop_event", "time": ms(ts + 2),
                     "event": {"type": "tool.call", "name": tool[0], "args": tool[1]}})
    recs.append({"type": "context.append_loop_event", "time": ms(ts + 5),
                 "event": {"type": "step.end", "finishReason": finish,
                           "usage": {"inputOther": 10, "output": 20_000,
                                     "inputCacheRead": 2_000_000, "inputCacheCreation": 0}}})
    # Kimi duplicates step usage in usage.record; it must not be billed twice.
    recs.append({"type": "usage.record", "time": ms(ts + 5), "usage": {"output": 20_000}})
    recs.append({"type": "turn.ended", "time": ms(ts + 6)})
    return recs


def test_kimi_stagnant_trigger_loop_is_flagged(env):
    tmp_path, _, _, kw = env
    wire = tmp_path / "kimi" / "wd_repo_ab12" / "session_1234abcd" / "agents" / "main" / "wire.jsonl"
    recs = [{"type": "profile.bind", "time": int((NOW - 7 * 3600) * 1000),
             "environmentDisclosure": {"cwd": "/repo"}}]
    recs += kimi_turn({"kind": "user"}, "babysit the release", NOW - 6 * 3600)
    for i in range(8):
        recs += kimi_turn({"kind": "system_trigger", "name": "subagent"}, "subagent finished",
                          NOW - 5 * 3600 + i * 1800)
    write(wire, recs)
    flagged, _ = runaway.scan(now=NOW, **kw)
    assert len(flagged) == 1
    e = flagged[0]
    assert e["engine"] == "kimi" and e["session_id"] == "session_1234abcd"
    assert e["cwd"] == "/repo" and e["title"] == "babysit the release"
    assert e["auto_turns"] == 8 and e["stagnant_turns"] == 8
    assert e["cost_tokens"] == 8 * (10 + 200_000 + 100_000)


def test_kimi_productive_loop_is_not_flagged(env):
    tmp_path, _, _, kw = env
    wire = tmp_path / "kimi" / "wd_r" / "session_feed" / "agents" / "main" / "wire.jsonl"
    recs = []
    for i in range(8):
        recs += kimi_turn({"kind": "system_trigger", "name": "subagent"}, "go on",
                          NOW - 5 * 3600 + i * 1800,
                          tool=("Shell", {"command": "git commit -m wip"}))
    write(wire, recs)
    assert runaway.scan(now=NOW, **kw)[0] == []


def devin_db(path, prompt, turns, start):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, working_directory TEXT, title TEXT,
                               created_at INTEGER, last_activity_at INTEGER);
        CREATE TABLE message_nodes (session_id TEXT, node_id INTEGER, created_at INTEGER,
                                    chat_message TEXT, UNIQUE(session_id, node_id));
    """)
    rows, node = [], 0

    def add(ts, msg):
        nonlocal node
        node += 1
        rows.append(("foamy-gallon", node, int(ts), json.dumps(msg)))

    metrics = {"input_tokens": 10, "output_tokens": 20_000, "cache_read_tokens": 2_000_000,
               "cache_creation_tokens": 0}
    for i in range(turns):
        ts = start + i * 1800
        add(ts, {"role": "user", "content": prompt, "metadata": {"is_user_input": True}})
        add(ts + 5, {"role": "assistant", "content": "", "metadata": {
            "finish_reason": "tool_calls", "metrics": metrics},
            "tool_calls": [{"name": "exec", "arguments": {"command": "gh run list"}}]})
        add(ts + 6, {"role": "tool", "content": "queued" + " " * (2 << 20 if i == 0 else 0)})
        add(ts + 10, {"role": "assistant", "content": "Still running.",
                      "metadata": {"finish_reason": "stop", "metrics": metrics}})
    con.executemany("INSERT INTO message_nodes VALUES (?, ?, ?, ?)", rows)
    con.execute("INSERT INTO sessions VALUES ('foamy-gallon', '/repo', 'Watch CI', ?, ?)",
                (int(start), int(rows[-1][2])))
    con.commit()
    con.close()


def test_devin_repeated_scripted_prompt_loop_is_flagged(env):
    tmp_path, _, _, kw = env
    db = tmp_path / "sessions.db"
    devin_db(db, "You are the CI orchestrator. Check the pipeline and report status.",
             9, NOW - 5 * 3600)
    kw["devin_dbs"] = [str(db)]
    flagged, _ = runaway.scan(now=NOW, **kw)
    assert len(flagged) == 1
    e = flagged[0]
    assert e["engine"] == "devin" and e["session_id"] == "devincli-foamy-gallon"
    assert e["cwd"] == "/repo"
    assert e["triggers"] == {"repeat": 8}  # the first send could be a person
    assert e["stagnant_turns"] == 8
    # An unchanged database is not queried again and keeps its verdict.
    again, _ = runaway.scan(now=NOW, **kw)
    assert [x["session_id"] for x in again] == ["devincli-foamy-gallon"]
    assert again[0]["auto_turns"] == 8


def test_devin_distinct_human_prompts_are_not_flagged(env):
    tmp_path, _, _, kw = env
    db = tmp_path / "sessions.db"
    devin_db(db, "short ask", 9, NOW - 5 * 3600)
    kw["devin_dbs"] = [str(db)]
    assert runaway.scan(now=NOW, **kw)[0] == []


def hermes_db(path, source, turns, start, totals):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, cwd TEXT, title TEXT,
            started_at REAL, last_activity_at REAL, input_tokens INTEGER,
            output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,
            content TEXT, tool_calls TEXT, finish_reason TEXT, timestamp REAL,
            display_kind TEXT);
    """)
    con.execute("INSERT INTO sessions VALUES ('hx1', ?, '/repo', 'Inbox sweep', ?, ?, 0, 0, 0, 0)",
                (source, start, start))
    con.commit()
    con.close()
    hermes_append(path, turns, start, totals)


def hermes_append(path, turns, start, totals):
    con = sqlite3.connect(path)
    for i in range(turns):
        ts = start + i * 1800
        call = [{"function": {"name": "terminal", "arguments": json.dumps({"command": "ls inbox"})}}]
        con.execute("INSERT INTO messages (session_id, role, content, timestamp) "
                    "VALUES ('hx1', 'user', 'check the inbox', ?)", (ts,))
        con.execute("INSERT INTO messages (session_id, role, tool_calls, finish_reason, timestamp)"
                    " VALUES ('hx1', 'assistant', ?, 'tool_calls', ?)", (json.dumps(call), ts + 5))
        con.execute("INSERT INTO messages (session_id, role, content, finish_reason, timestamp) "
                    "VALUES ('hx1', 'assistant', 'nothing new', 'stop', ?)", (ts + 10,))
        last = ts + 10
    con.execute("UPDATE sessions SET last_activity_at = ?, input_tokens = ?, output_tokens = ?,"
                " cache_read_tokens = ?, cache_write_tokens = ?", (last, *totals))
    con.commit()
    con.close()


def test_hermes_cron_loop_is_flagged_and_billed_by_session_totals(env):
    tmp_path, _, _, kw = env
    db = tmp_path / "state.db"
    hermes_db(db, "cron", 6, NOW - 5 * 3600, (100, 1_000, 1_000_000, 0))
    kw["hermes_dbs"] = [str(db)]
    flagged, watch = runaway.scan(now=NOW, debug=True, **kw)
    assert flagged == []  # cold start: no usage baseline yet
    assert watch[0]["triggers"] == {"timer": 6}
    hermes_append(db, 2, NOW - 2 * 3600, (110, 200_000, 21_000_000, 0))
    flagged, _ = runaway.scan(now=NOW, **kw)
    assert len(flagged) == 1
    e = flagged[0]
    assert e["engine"] == "hermes" and e["session_id"] == "hx1"
    assert e["auto_turns"] == 8 and e["stagnant_turns"] == 8
    assert e["cost_tokens"] == 10 + 5 * 199_000 + 2_000_000


def test_hermes_cli_session_with_human_prompts_is_not_flagged(env):
    tmp_path, _, _, kw = env
    db = tmp_path / "state.db"
    hermes_db(db, "cli", 6, NOW - 5 * 3600, (0, 0, 0, 0))
    hermes_append(db, 4, NOW - 2 * 3600, (0, 10_000_000, 90_000_000, 0))
    kw["hermes_dbs"] = [str(db)]
    assert runaway.scan(now=NOW, **kw)[0] == []
