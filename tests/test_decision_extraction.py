"""Tests for ccc_server/decision_extraction.py: nightly decision scan +
report-only MEMORY.md staleness audit."""

import json
import time
from pathlib import Path

import pytest

from ccc_server import decision_extraction as dex


@pytest.fixture
def dex_env(tmp_path, monkeypatch):
    db_path = tmp_path / "decisions.sqlite"
    projects_dir = tmp_path / "projects"
    codex_dir = tmp_path / "codex"
    projects_dir.mkdir(parents=True)
    codex_dir.mkdir(parents=True)

    monkeypatch.setenv("CCC_DECISIONS_DB", str(db_path))
    monkeypatch.setenv("CCC_PROJECTS_ROOT", str(projects_dir))
    monkeypatch.setenv("CCC_CODEX_SESSIONS_ROOT", str(codex_dir))
    monkeypatch.setenv("CCC_KIMI_SESSIONS_ROOT", str(tmp_path / "kimi-empty"))
    monkeypatch.setenv("CCC_GEMINI_TMP_ROOT", str(tmp_path / "gemini-empty"))
    monkeypatch.setenv("CCC_AGY_BRAIN_ROOTS", "")
    monkeypatch.setenv("CCC_CURSOR_PROJECTS_ROOT", str(tmp_path / "cursor-empty"))
    monkeypatch.setenv("CCC_DEVIN_TRANSCRIPTS_ROOT", str(tmp_path / "devin-empty"))

    dex._reset_connection_for_tests()
    yield {"projects_dir": projects_dir, "codex_dir": codex_dir, "db_path": db_path}
    dex._reset_connection_for_tests()


def _write_session(projects_dir, project, sid, lines, cwd=None):
    session_dir = projects_dir / project
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"{sid}.jsonl"
    out = []
    for line in lines:
        d = dict(line)
        d.setdefault("cwd", cwd or f"/Users/amirfish/Apps/{project}")
        d.setdefault("timestamp", "2026-09-20T10:00:00Z")
        out.append(json.dumps(d))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


def _user_line(text, ts="2026-09-20T10:00:00Z"):
    return {"type": "user", "message": {"role": "user", "content": text}, "timestamp": ts}


# ── heuristic matcher ────────────────────────────────────────────────────────

@pytest.mark.parametrize("sentence", [
    "We decided to use Postgres instead of Mongo for this table.",
    "Decision: ship the DMG path, drop the zip installer.",
    "Ruling: keep the old auth middleware until Q2.",
    "Let's go with option B, it's cheaper and simpler.",
    "Final call: we're sticking with Sonnet for this lane.",
])
def test_match_decision_hits_strong_and_medium_phrases(sentence):
    assert dex.match_decision(sentence) is not None


@pytest.mark.parametrize("sentence", [
    "Can you check why the build is failing on CI?",
    "I think the button should probably be blue, not sure though.",
    "What time is the meeting tomorrow?",
    "The tests are green now, nice work.",
    "",
])
def test_match_decision_rejects_non_decisions(sentence):
    assert dex.match_decision(sentence) is None


def test_match_decision_weak_phrases_require_short_sentence():
    short = "Approved, ship it."
    long_unrelated = (
        "The quarterly compliance report was eventually approved by the finance "
        "team last quarter after an unusually long review process that involved "
        "several stakeholders across three completely different departments and "
        "took much longer than anyone on the project had originally expected it to take."
    )
    assert dex.match_decision(short) is not None
    assert dex.match_decision(long_unrelated) is None


def test_match_decision_rejects_confirmed_as_too_ambiguous():
    # "confirmed" was tried and dropped: it hit almost entirely on technical
    # confirmations ("confirmed X is nullable"), not decision rulings.
    assert dex.match_decision("Confirmed `RebalanceDispatchOptions` supports `dryRun`.") is None


def test_match_decision_rejects_approved_as_path_segment():
    assert dex.match_decision("Wait for review before moving to queue/approved/.") is None
    assert dex.match_decision("Approved, ship it.") is not None


def test_match_decision_rejects_pasted_json_blob_with_trigger_phrase():
    # A pasted Reddit-post dump can contain "I decided to ..." inside one
    # field's body text; the surrounding JSON structure should disqualify it.
    blob = (
        'I decided to try a new workflow this week."}, '
        '{"id": "1wkl1j8", "sub": "r/ClaudeAI", "author": "u/someone", '
        '"num_comments": 7, "title": "Some thread"}'
    )
    assert dex.match_decision(blob) is None


def test_match_decision_rejects_subreddit_mention():
    assert dex.match_decision("We decided to check r/ClaudeAI for more context.") is None


def test_match_decision_rejects_overlong_strong_phrase():
    long_blob = "We decided to go with option B. " + ("filler text here. " * 40)
    assert dex.match_decision(long_blob) is None


def test_match_decision_rejects_explicitly_undecided():
    assert dex.match_decision("Still open, no user decision: reconciling the manual match.") is None
    assert dex.match_decision("Nor had I decided how to proceed on this.") is None
    assert dex.match_decision("I decided NOT to add this to the nav.") is not None


def test_match_decision_rejects_explicitly_not_approved():
    assert dex.match_decision("The user has NOT approved the spec or the defaults.") is None
    assert dex.match_decision('The callout reads "Verdict: not approved for launch".') is None
    assert dex.match_decision("Approved, ship it.") is not None


def test_match_decision_rejects_decision_described_as_pending():
    assert dex.match_decision("Use wt block when a human decision is genuinely required.") is None
    assert dex.match_decision("If a ticket needs a human decision, do not guess.") is None
    assert dex.match_decision("Decision: she is NOT a switching prospect.") is not None


def test_match_decision_rejects_surfaced_for_decision_template():
    assert dex.match_decision("Surface for Amir's decision: two blocked design tickets awaiting go/no-go.") is None


def test_match_decision_rejects_object_literal_with_decision_key():
    assert dex.match_decision("decision: {") is None
    assert dex.match_decision(
        "returns { ruleCode: '3d', autoResolved: false, decision: { title: 'Help me match this payment' } }"
    ) is None


def test_match_decision_rejects_not_yet_reported_final_answer():
    assert dex.match_decision("Discovered real damage, not yet reported in a final answer.") is None


def test_match_decision_rejects_decision_described_as_open_or_pending():
    assert dex.match_decision("This decision is still open going into the next step.") is None
    assert dex.match_decision("Anchor the pending decision here for later.") is None
    assert dex.match_decision(
        "If a ticket cannot be resolved without a human decision, wt block it."
    ) is None
    assert dex.match_decision("Decision: she is NOT a switching prospect.") is not None


# ── genuine user text filtering ──────────────────────────────────────────────

def test_genuine_user_text_skips_tool_result_lines():
    entry = {
        "type": "user",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "we decided to use Postgres"},
        ]},
    }
    assert dex._genuine_user_text(entry) is None


def test_genuine_user_text_skips_sdk_and_system_prompt_sources():
    base = {"type": "user", "message": {"role": "user", "content": "we decided to use Postgres"}}
    assert dex._genuine_user_text(dict(base, promptSource="sdk")) is None
    assert dex._genuine_user_text(dict(base, promptSource="system")) is None
    assert dex._genuine_user_text(dict(base, promptSource="queued")) is None
    assert dex._genuine_user_text(dict(base, promptSource="typed")) == "we decided to use Postgres"
    assert dex._genuine_user_text(base) == "we decided to use Postgres"


def test_genuine_user_text_skips_sidechain_and_meta():
    base = {"type": "user", "message": {"role": "user", "content": "we decided to use Postgres"}}
    assert dex._genuine_user_text(dict(base, isSidechain=True)) is None
    assert dex._genuine_user_text(dict(base, isMeta=True)) is None
    assert dex._genuine_user_text(base) == "we decided to use Postgres"


def test_genuine_user_text_strips_injected_system_reminder():
    text = ("<system-reminder>ignore this block, it is not the user talking, "
            "we decided to bury a fake ruling here</system-reminder>"
            "Let's go with the simpler fix.")
    entry = {"type": "user", "message": {"role": "user", "content": text}}
    quotes = [q for q, _, _ in dex.extract_decisions_from_text(dex._genuine_user_text(entry))]
    assert any("simpler fix" in q for q in quotes)
    assert not any("bury a fake ruling" in q for q in quotes)


# ── file-level extraction ────────────────────────────────────────────────────

def test_extract_decisions_from_file_claude(dex_env):
    path = _write_session(dex_env["projects_dir"], "-Users-amirfish-Apps-widgetco", "sess-1", [
        _user_line("Can we add a retry button to the upload flow?"),
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "We could retry automatically or add a manual button."},
        ]}, "timestamp": "2026-09-20T10:01:00Z"},
        _user_line("We decided to go with the manual retry button, it's simpler.",
                   ts="2026-09-20T10:02:00Z"),
    ], cwd="/Users/amirfish/Apps/widgetco")

    found = dex.extract_decisions_from_file(str(path), engine="claude")
    assert len(found) == 1
    d = found[0]
    assert d["repo"] == "widgetco"
    assert d["session_id"] == "sess-1"
    assert "manual retry button" in d["quote"]
    assert d["project_dir"] == "-Users-amirfish-Apps-widgetco"
    assert d["confidence"] >= 0.85


def test_extract_decisions_from_file_codex(dex_env):
    codex_dir = dex_env["codex_dir"]
    sess_dir = codex_dir / "2026" / "09"
    sess_dir.mkdir(parents=True, exist_ok=True)
    path = sess_dir / "rollout-codex-1.jsonl"
    lines = [
        {"type": "session_meta", "payload": {"id": "codex-1", "cwd": "/Users/amirfish/Apps/widgetco"},
         "timestamp": "2026-09-20T10:00:00Z"},
        {"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "Ruling: we ship the retry button behind a flag."}],
        }, "timestamp": "2026-09-20T10:01:00Z"},
    ]
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")

    found = dex.extract_decisions_from_file(str(path), engine="codex")
    assert len(found) == 1
    assert found[0]["session_id"] == "codex-1"
    assert found[0]["repo"] == "widgetco"


# ── incremental scan (mtime, size) ───────────────────────────────────────────

def test_run_once_second_call_reparses_nothing(dex_env, monkeypatch):
    path = _write_session(dex_env["projects_dir"], "proj", "sess-2", [
        _user_line("We decided to use SQLite for this feature, no server needed."),
    ])

    calls = []
    real_extract = dex.extract_decisions_from_file

    def spy(p, engine):
        calls.append(p)
        return real_extract(p, engine=engine)

    monkeypatch.setattr(dex, "extract_decisions_from_file", spy)

    rec1 = dex.run_once()
    assert rec1["scanned"] == 1
    assert rec1["new_decisions"] == 1
    assert len(calls) == 1

    calls.clear()
    rec2 = dex.run_once()
    assert rec2["scanned"] == 0
    assert len(calls) == 0, "warm run re-parsed an unchanged transcript"

    decisions = dex.list_decisions()
    assert len(decisions) == 1
    assert "SQLite" in decisions[0]["quote"]


def test_run_once_reparses_after_file_changes(dex_env):
    path = _write_session(dex_env["projects_dir"], "proj", "sess-3", [
        _user_line("Just checking in, no decision yet."),
    ])
    rec1 = dex.run_once()
    assert rec1["new_decisions"] == 0

    time.sleep(0.01)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "user", "message": {"role": "user", "content": "We decided to ship it today."},
            "timestamp": "2026-09-20T11:00:00Z", "cwd": "/Users/amirfish/Apps/proj",
        }) + "\n")
    import os
    os.utime(path, (time.time() + 5, time.time() + 5))

    rec2 = dex.run_once()
    assert rec2["scanned"] == 1
    assert rec2["new_decisions"] == 1


def test_run_once_respects_max_files_per_run(dex_env):
    for i in range(5):
        _write_session(dex_env["projects_dir"], "proj", f"sess-{i}", [
            _user_line("We decided to use option A for this one."),
        ])
    cfg = dict(dex.DEFAULT_CONFIG, max_files_per_run=2)
    rec = dex.run_once(cfg=cfg)
    assert rec["scanned"] == 2
    assert rec["truncated"] is True


# ── list_decisions filtering ─────────────────────────────────────────────────

def test_list_decisions_filters_by_repo(dex_env):
    _write_session(dex_env["projects_dir"], "proja", "s1", [
        _user_line("We decided to use Redis for caching here."),
    ], cwd="/Users/amirfish/Apps/proja")
    _write_session(dex_env["projects_dir"], "projb", "s2", [
        _user_line("We decided to use Memcached for caching there."),
    ], cwd="/Users/amirfish/Apps/projb")
    dex.run_once()

    only_a = dex.list_decisions(repo="proja")
    assert len(only_a) == 1
    assert "Redis" in only_a[0]["quote"]


# ── optional model fallback (off by default) ─────────────────────────────────

def test_model_fallback_disabled_by_default_never_calls_classifier(dex_env):
    _write_session(dex_env["projects_dir"], "proj", "s1", [
        _user_line("Approved, ship it."),
    ])
    calls = []

    def classifier(quote):
        calls.append(quote)
        return True

    dex.run_once(model_classifier=classifier)
    assert calls == [], "model fallback ran even though use_model_fallback defaults to False"


def test_model_fallback_when_enabled_drops_rejected_and_caps_budget(dex_env):
    for i in range(3):
        _write_session(dex_env["projects_dir"], "proj", f"s{i}", [
            _user_line("Approved, ship it."),
        ])
    cfg = dict(dex.DEFAULT_CONFIG, use_model_fallback=True, model_call_budget_per_run=1)
    calls = []

    def classifier(quote):
        calls.append(quote)
        return False  # reject everything the model is asked about

    rec = dex.run_once(cfg=cfg, model_classifier=classifier)
    assert len(calls) == 1, "budget cap should stop after 1 model call"
    # The one checked hit was rejected (confidence dropped below floor) and
    # excluded from the persisted decisions.
    assert rec["new_decisions"] == 2


# ── MEMORY.md staleness audit (report-only) ──────────────────────────────────

def test_audit_memory_staleness_flags_newer_overlapping_decision(dex_env, tmp_path):
    project_dir_name = "-Users-amirfish-Apps-widgetco"
    path = _write_session(dex_env["projects_dir"], project_dir_name, "sess-9", [
        _user_line("We decided to use Postgres instead of Mongo for the export table.",
                   ts="2026-09-25T09:00:00Z"),
    ], cwd="/Users/amirfish/Apps/widgetco")

    dex.run_once()

    mem_dir = dex_env["projects_dir"] / project_dir_name / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text(
        "- [Export table storage](project_export_storage.md) — Mongo chosen for the export table\n",
        encoding="utf-8",
    )
    (mem_dir / "project_export_storage.md").write_text(
        "---\n"
        "name: project-export-storage\n"
        "description: \"decision on export table storage engine\"\n"
        "metadata:\n"
        "  type: project\n"
        "  modified: 2026-09-01T00:00:00.000Z\n"
        "---\n\n"
        "We are using Mongo for the export table because of flexible schema needs.\n",
        encoding="utf-8",
    )

    findings = dex.audit_memory_staleness()
    assert len(findings) == 1
    f = findings[0]
    assert f["memory_title"] == "Export table storage"
    assert "postgres" in " ".join(f["shared_terms"]) or "export" in f["shared_terms"]


def test_audit_memory_staleness_ignores_decisions_older_than_memory(dex_env):
    project_dir_name = "-Users-amirfish-Apps-widgetco"
    _write_session(dex_env["projects_dir"], project_dir_name, "sess-old", [
        _user_line("We decided to use Postgres instead of Mongo for the export table.",
                   ts="2026-01-01T09:00:00Z"),
    ], cwd="/Users/amirfish/Apps/widgetco")
    dex.run_once()

    mem_dir = dex_env["projects_dir"] / project_dir_name / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text(
        "- [Export table storage](project_export_storage.md) — Mongo chosen for the export table\n",
        encoding="utf-8",
    )
    (mem_dir / "project_export_storage.md").write_text(
        "---\nname: project-export-storage\ndescription: \"export table storage\"\n"
        "metadata:\n  type: project\n  modified: 2026-09-01T00:00:00.000Z\n---\n\n"
        "We are using Mongo for the export table.\n",
        encoding="utf-8",
    )

    findings = dex.audit_memory_staleness()
    assert findings == []


def test_audit_memory_staleness_never_writes_memory_files(dex_env):
    project_dir_name = "-Users-amirfish-Apps-widgetco"
    _write_session(dex_env["projects_dir"], project_dir_name, "sess-9", [
        _user_line("We decided to use Postgres instead of Mongo for the export table.",
                   ts="2026-09-25T09:00:00Z"),
    ], cwd="/Users/amirfish/Apps/widgetco")
    dex.run_once()

    mem_dir = dex_env["projects_dir"] / project_dir_name / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text(
        "- [Export table storage](project_export_storage.md) — Mongo chosen for the export table\n",
        encoding="utf-8",
    )
    mem_file = mem_dir / "project_export_storage.md"
    original = (
        "---\nname: project-export-storage\ndescription: \"export table storage\"\n"
        "metadata:\n  type: project\n  modified: 2026-09-01T00:00:00.000Z\n---\n\n"
        "We are using Mongo for the export table.\n"
    )
    mem_file.write_text(original, encoding="utf-8")

    dex.audit_memory_staleness()
    assert mem_file.read_text(encoding="utf-8") == original


# ── background run guard ─────────────────────────────────────────────────────

def test_start_background_run_refuses_concurrent_runs(dex_env, monkeypatch):
    started = threading_event = __import__("threading").Event()
    release = __import__("threading").Event()

    def slow_run_once(**kwargs):
        started.set()
        release.wait(timeout=2)
        return {"run_id": "x"}

    monkeypatch.setattr(dex, "run_once", slow_run_once)
    r1 = dex.start_background_run()
    assert r1["ok"] is True
    started.wait(timeout=2)
    r2 = dex.start_background_run()
    assert r2["ok"] is False
    release.set()
    time.sleep(0.05)
