"""Soft-block detector (Attention API) — behavior + perf guards.

CCC's formal flags (question_waiting / needs_approval) only fire on the
AskUserQuestion tool and permission prompts. Real agents far more often END A
TURN WITH A PROSE QUESTION — "paused for review", "want me to…", "pick one",
"plan before I write?". The detector flags a TERMINAL session whose last
assistant turn is awaiting the human. See docs/attention-api.md.

These cases are FROZEN archetypes of the live fixture the task was validated
against. Live sessions mutate between sweeps (a paused session resumes, a
question scrolls out of the last turn), so the durable test encodes each
archetype as a synthetic row rather than asserting against moving live state.
"""
import importlib
import time
import uuid

import pytest

server = importlib.import_module("server")


def _row(text, **over):
    """A terminal (awaiting-human) session row by default — overridable."""
    r = {
        "session_id": str(uuid.uuid4()),
        "source": "interactive",
        "engine": "claude",
        "is_live": False,
        "last_event_type": "assistant",
        "pending_tool": None,
        "subagent_in_flight_count": 0,
        "sidecar_in_flight": False,
        "sidecar_status": None,
        "question_waiting": False,
        "needs_approval": False,
        "last_assistant_text": text,
        "session_state": server._parse_session_state(text),
    }
    r.update(over)
    return r


# ── MUST flag — terminal sessions awaiting the human (the core bug) ───────────

MUST_FLAG = {
    "paused_for_review":
        "Schema drafted. Paused for your review — let me know if it looks right.",
    "trailing_question":
        "I went 5 over 10 on the rubric. Does the reframe land for you?",
    "plan_before_writing":
        "Here's the plan before I write any code. Want me to proceed?",
    "please_review_spec":
        "Please review the spec and let me know before I move on.",
    "want_me_to":
        "I can wire it next. Want me to model the payments flow?",
    "pick_one_of_n":
        ("Three directions:\n1. Go deep on market\n2. Prototype the switch\n"
         "3. Ship the preset\nWhich one do you want?"),
    "pick_a_route":
        ("Two routes for mobile:\n- Tailscale + PWA\n- Native shell\n"
         "Which route should I take?"),
}


@pytest.mark.parametrize("name,text", sorted(MUST_FLAG.items()))
def test_detector_flags_prose_questions(name, text):
    sb = server._detect_soft_block(_row(text))
    assert sb is not None, f"{name}: detector missed a prose block — {text!r}"
    assert sb["score"] >= 3, f"{name}: score {sb['score']} below threshold"
    assert sb["question_text"], f"{name}: no question_text extracted"


# ── Guaranteed hits — the formal flags are still honored ──────────────────────

def test_formal_question_waiting_is_guaranteed():
    sb = server._detect_soft_block(_row("anything", question_waiting=True,
                                        question_text="Pick an option"))
    assert sb and sb["guaranteed"] and sb["score"] == 99


def test_formal_needs_approval_is_guaranteed():
    sb = server._detect_soft_block(_row("anything", needs_approval=True,
                                        needs_approval_message="Approve push?"))
    assert sb and sb["guaranteed"]


# ── MUST NOT flag ─────────────────────────────────────────────────────────────

def test_working_subagent_not_flagged():
    """A session whose sub-agent is still running is WORKING, not waiting —
    even if its text contains a question. The terminal gate excludes it."""
    r = _row("Spawning agents. Which path? Working on it.",
             subagent_in_flight_count=2)
    assert server._detect_soft_block(r) is None


def test_pending_tool_not_flagged():
    r = _row("Running the build? hold on.", pending_tool="Bash")
    assert server._detect_soft_block(r) is None


def test_mid_write_not_flagged():
    r = _row("Editing now. Want me to continue?", sidecar_in_flight=True)
    assert server._detect_soft_block(r) is None


def test_done_shipped_not_flagged():
    r = _row("Done. Shipped it. Smoke-tested and live. Nothing left to do.")
    assert server._detect_soft_block(r) is None


def test_user_replied_last_not_flagged():
    """If the last meaningful event is the user, we're awaiting the assistant."""
    r = _row("Want me to proceed?", last_event_type="user")
    assert server._detect_soft_block(r) is None


def test_plain_statement_not_flagged():
    r = _row("The current checkout is on main and ahead of origin. No push.")
    assert server._detect_soft_block(r) is None


# ── COO feed precision gate: coarse live+idle (sidecar_waiting) ────────────────

def test_feed_gate_keeps_decision_request():
    """A live+idle session whose next_step asks for a DECISION stays in."""
    r = _row("Locked the content.",
             is_live=True, sidecar_status="waiting",
             session_state={"did": "x", "insight": "y",
                            "next_step_user": "Review the post; approve or redirect."})
    assert server._awaits_human_decision(r) is True


def test_feed_gate_drops_external_handoff():
    """A session that handed the human an external task is not an immediate
    decision — drop the coarse live+idle row (it shipped its part)."""
    r = _row("Scaffolded the KB.",
             is_live=True, sidecar_status="waiting",
             session_state={"did": "x", "insight": "y",
                            "next_step_user": "Export your data, unzip the CSVs, "
                                              "then say 'ingest my export'."})
    assert server._awaits_human_decision(r) is False


def test_feed_gate_drops_idle_loop_worker():
    """A loop worker idling between tickets ('say push whenever') is not
    awaiting a decision."""
    r = _row('Idle and waiting — I\'ll pick up the next ticket. '
             'Say "push" whenever you want them sent.',
             is_live=True, sidecar_status="waiting", session_state=None)
    assert server._awaits_human_decision(r) is False


# ── State labels ──────────────────────────────────────────────────────────────

def test_state_label_working():
    assert server._session_state_label(
        _row("x", is_live=True, pending_tool="Bash")) == "working"


def test_state_label_waiting():
    # waiting is only meaningful for a LIVE session — a dead process is ended.
    assert server._session_state_label(
        _row("Want me to proceed?", is_live=True)) == "waiting"


def test_state_label_not_live_with_question_is_ended():
    # Dead process whose last turn ended in a question: ended, not waiting.
    assert server._session_state_label(
        _row("Want me to proceed?")) == "ended"


def test_state_label_idle():
    # idle = LIVE but nothing in flight (Stop fired / sitting at the prompt).
    assert server._session_state_label(
        _row("All done, nothing pending.", is_live=True)) == "idle"


def test_state_label_ended():
    # ended = not live (process gone / liveness window expired). _row defaults
    # to is_live=False.
    assert server._session_state_label(
        _row("All done, nothing pending.")) == "ended"


def test_state_label_stale_active_is_idle_not_working():
    # The #1 reliability bug: a live session whose last tool finished long ago
    # left sidecar_status=='active' on disk (Stop hook never fired). Bounded by
    # _WORKING_GAP_WINDOW, that must read idle, not working.
    assert server._session_state_label(_row(
        "interrupted mid-run", is_live=True, sidecar_status="active",
        sidecar_ts=time.time() - 27 * 60)) == "idle"


# ── Turn reader collapses tool calls cheaply ─────────────────────────────────

def test_turn_reader_collapses_tools(tmp_path):
    import json
    sid = str(uuid.uuid4())
    p = tmp_path / f"{sid}.jsonl"
    lines = [
        {"type": "user", "message": {"role": "user", "content": "do the thing"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "on it"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Want me to proceed?"}]}},
    ]
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    turns = server._attention_read_turns(p, n=3)
    assert turns[0] == {"role": "user", "text": "do the thing"}
    assert "[tool:Bash]" in turns[1]["text"]
    assert turns[-1]["text"] == "Want me to proceed?"


# ── Feed tuning: recency window, backlog exclusion, P1 always kept ────────────

def _conv(kind, priority, *, modified, is_live=False):
    """A conv row carrying the classifier result inline for the stub below."""
    return {"session_id": str(uuid.uuid4()), "modified": modified,
            "is_live": is_live, "folder_label": "repo", "jsonl_path": "/x.jsonl",
            "_kind": kind, "_priority": priority}


def _stub_feed(monkeypatch, rows):
    """Drive compute_attention_feed off `rows` with a controlled classifier so
    we test the feed's own filtering (recency / exclude / P1), not git state."""
    monkeypatch.setattr(
        server,
        "_archive_all_rows_cached",
        lambda options: (rows, True),
    )
    monkeypatch.setattr(server, "_attention_read_turns", lambda *a, **k: [])

    def classify(c):
        if not c.get("_kind"):
            return None
        return {"kind": c["_kind"], "priority": c["_priority"],
                "session_id": c["session_id"], "name": "n", "where": "w"}
    monkeypatch.setattr(server, "_classify_attention", classify)


def test_attention_feed_reuses_lightweight_archive_snapshot(monkeypatch):
    now = time.time()
    rows = [_conv("soft_block", 2, modified=now)]
    calls = []

    def cached_rows(options):
        calls.append(options)
        return rows, True

    monkeypatch.setattr(server, "_archive_all_rows_cached", cached_rows)
    monkeypatch.setattr(
        server,
        "find_all_conversations",
        lambda **kwargs: pytest.fail("attention bypassed the archive snapshot"),
    )
    monkeypatch.setattr(server, "_attention_read_turns", lambda *a, **k: [])
    monkeypatch.setattr(
        server,
        "_classify_attention",
        lambda c: {
            "kind": c["_kind"],
            "priority": c["_priority"],
            "session_id": c["session_id"],
            "name": "n",
            "where": "w",
        },
    )

    result = server.compute_attention_feed()

    assert result["shown"] == 1
    assert calls == [{
        "include_prs": False,
        "resolve_pr_states": False,
        "resolve_effective": False,
        "resolve_worktree_dirty": False,
    }]


def test_feed_default_excludes_committed_not_pushed(monkeypatch):
    now = time.time()
    rows = [_conv("committed_not_pushed", 5, modified=now),
            _conv("soft_block", 2, modified=now)]
    _stub_feed(monkeypatch, rows)
    default = server.compute_attention_feed()
    kinds = {i["kind"] for i in default["items"]}
    assert kinds == {"soft_block"}, "git-hygiene rows must be out of the default feed"
    # scope=all keeps the full pool.
    allf = server.compute_attention_feed(recent_only=False)
    assert {i["kind"] for i in allf["items"]} == {"committed_not_pushed", "soft_block"}


def test_feed_default_time_bounds_soft_block(monkeypatch):
    now = time.time()
    stale = now - 100 * 3600   # ~4 days old → outside the 48h window
    rows = [_conv("soft_block", 2, modified=stale),
            _conv("soft_block", 2, modified=now)]
    _stub_feed(monkeypatch, rows)
    default = server.compute_attention_feed()
    assert default["shown"] == 1, "stale soft_block should be dropped from default"
    assert server.compute_attention_feed(recent_only=False)["shown"] == 2, \
        "scope=all still returns the stale ones for power use"


def test_feed_keeps_priority1_regardless_of_age(monkeypatch):
    now = time.time()
    old = now - 100 * 3600
    rows = [_conv("question_blocked", 1, modified=old)]
    _stub_feed(monkeypatch, rows)
    assert server.compute_attention_feed()["shown"] == 1, "P1 must always be kept"


def test_feed_keeps_live_even_when_old_mtime(monkeypatch):
    now = time.time()
    old = now - 100 * 3600
    rows = [_conv("soft_block", 2, modified=old, is_live=True)]
    _stub_feed(monkeypatch, rows)
    assert server.compute_attention_feed()["shown"] == 1, "live sessions stay in"


# ── PERF: cross-repo feed must not read tails for every row ───────────────────

def test_attention_feed_bounds_turn_reads(monkeypatch):
    """The feed enriches turns for at most the output cap, never all rows.
    Reading a tail per attention row was the class of bug perf gates exist for.
    """
    n = 300
    now = time.time()
    rows = []
    for i in range(n):
        # Every row is a terminal prose block → every row classifies as
        # soft_block, so without a cap the feed would read 300 file tails.
        rows.append(_row("Want me to proceed?", session_id=str(uuid.uuid4()),
                         jsonl_path=f"/nonexistent/{i}.jsonl",
                         folder_label="repo", modified=now - i))
    monkeypatch.setattr(
        server,
        "_archive_all_rows_cached",
        lambda options: (rows, True),
    )

    reads = []
    orig = server._attention_read_turns
    monkeypatch.setattr(server, "_attention_read_turns",
                        lambda *a, **k: reads.append(a) or [])

    out = server.compute_attention_feed()
    assert out["shown"] == n, "every terminal prose row should be flagged"
    assert len(reads) <= server._ATTENTION_FEED_TURN_CAP, (
        f"turn reader called {len(reads)}x — the feed turn-enrichment cap "
        "regressed (would read a file tail per attention row)"
    )
