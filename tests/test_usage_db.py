"""Tests for the unified session-usage DB (ccc_server.usage_db).

Fixtures are tiny synthetic transcripts with fake ids; no real conversation
content. Each engine's tests pin the verified quirks the adapter relies on.
"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone

from ccc_server.usage_db import ingest, pricing, queries, schema
from ccc_server.usage_db.adapters import claude_code, codex, kimi
from ccc_server.usage_db.models import family_and_label, pricing_key
from ccc_server.usage_db.types import SourceFile


def _write(path, records, raw_tail=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
        if raw_tail:
            fh.write(raw_tail)


def _sf(engine, path, **hint):
    st = os.stat(path)
    return SourceFile(engine, path, st.st_size, st.st_mtime_ns, hint)


def _claude_msg(mid, model, ts, inp=10, cr=0, cc=0, cc1h=0, out=5, uuid=None, block=None, sid="s1"):
    usage = {"input_tokens": inp, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cc,
             "output_tokens": out,
             "cache_creation": {"ephemeral_1h_input_tokens": cc1h, "ephemeral_5m_input_tokens": cc - cc1h}}
    return {"type": "assistant", "timestamp": ts, "sessionId": sid, "cwd": "/work/proj", "gitBranch": "main",
            "version": "9.9.9", "uuid": uuid or f"u-{mid}",
            "message": {"id": mid, "model": model, "usage": usage,
                        "content": block or [{"type": "text", "text": "x"}]}}


def _claude_user(ts, text="hi", sid="s1"):
    return {"type": "user", "timestamp": ts, "sessionId": sid, "message": {"role": "user", "content": text}}


class UsageDbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="usage-db-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.claude = os.path.join(self.tmp, "claude", "projects")
        self.codex = os.path.join(self.tmp, "codex")
        self.kimi = os.path.join(self.tmp, "kimi")
        self.conn = schema.connect(os.path.join(self.tmp, "t.sqlite3"))
        schema.migrate(self.conn)
        self.addCleanup(self.conn.close)

    def roots(self):
        return {"claude_code": self.claude, "codex": self.codex, "kimi": self.kimi}

    def run_ingest(self, **kw):
        return ingest.ingest(self.conn, self.roots(), **kw)

    def one(self, sql, *a):
        return self.conn.execute(sql, a).fetchone()

    def add_rate(self, key, inp, cr, cw5, out, cw1h=None, eff="2026-01-01", to=None):
        self.conn.execute(
            "INSERT INTO price_rates (provider, pricing_key, effective_from, effective_to, input_rate, "
            "cache_read_rate, cache_write_5m_rate, cache_write_1h_rate, output_rate) VALUES (?,?,?,?,?,?,?,?,?)",
            ("t", key, eff, to, inp, cr, cw5, cw1h, out))
        self.conn.commit()


class ModelTests(unittest.TestCase):
    def test_pricing_key_normalization(self):
        self.assertEqual(pricing_key("kimi-code/k3"), "k3")
        self.assertEqual(pricing_key("claude-haiku-4-5-20251001"), "claude-haiku-4-5")
        self.assertEqual(pricing_key("claude-opus-4-8[1m]"), "claude-opus-4-8")
        self.assertEqual(pricing_key("kimi-k2.7-code"), "kimi-k2-7-code")
        self.assertIsNone(pricing_key(None))

    def test_family_only_when_grounded(self):
        self.assertEqual(family_and_label("claude-sonnet-5"), ("Sonnet", "Sonnet 5"))
        self.assertEqual(family_and_label("claude-opus-4-8")[0], "Opus")
        self.assertEqual(family_and_label("gpt-5.6-terra")[0], "GPT")
        self.assertEqual(family_and_label("kimi-code/k3")[0], "Kimi")
        self.assertEqual(family_and_label("mystery-model"), (None, None))


class ClaudeAdapterTests(UsageDbCase):
    def test_message_lines_deduped_and_normalized(self):
        p = os.path.join(self.claude, "proj", "s1.jsonl")
        _write(p, [
            _claude_user("2026-09-01T10:00:00.000Z"),
            # one API message written as two content-block lines with identical usage
            _claude_msg("m1", "claude-sonnet-5", "2026-09-01T10:00:05.000Z", inp=100, cr=1000, cc=300, cc1h=200, out=50,
                        uuid="a", block=[{"type": "tool_use", "id": "t"}]),
            _claude_msg("m1", "claude-sonnet-5", "2026-09-01T10:00:05.500Z", inp=100, cr=1000, cc=300, cc1h=200, out=50,
                        uuid="b", block=[{"type": "text", "text": "y"}]),
            {"type": "user", "timestamp": "2026-09-01T10:00:06.000Z",
             "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
            {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-09-01T10:01:00.000Z"},
            _claude_msg("synth", "<synthetic>", "2026-09-01T10:02:00.000Z", inp=0, out=0),
        ])
        [ps] = claude_code.parse(_sf("claude_code", p))
        self.assertEqual(len(ps.events), 1)
        e = ps.events[0]
        self.assertEqual((e.input_tokens, e.cache_read_tokens, e.cache_creation_tokens, e.cache_creation_1h_tokens,
                          e.output_tokens), (100, 1000, 300, 200, 50))
        self.assertEqual(ps.assistant_message_count, 1)
        self.assertEqual(ps.user_message_count, 1)  # the tool_result is not a user message
        self.assertEqual(ps.tool_call_count, 1)
        self.assertEqual(ps.compaction_count, 1)
        self.assertEqual((ps.working_directory, ps.git_branch, ps.source_format_version), ("/work/proj", "main", "9.9.9"))
        self.assertEqual(ps.started_at, "2026-09-01T10:00:00.000Z")

    def test_subagent_is_own_row_linked_to_parent(self):
        _write(os.path.join(self.claude, "proj", "s1.jsonl"),
               [_claude_user("2026-09-01T10:00:00.000Z"), _claude_msg("m1", "claude-sonnet-5", "2026-09-01T10:00:05.000Z")])
        _write(os.path.join(self.claude, "proj", "s1", "subagents", "agent-abc.jsonl"),
               [_claude_msg("m2", "claude-haiku-4-5-20251001", "2026-09-01T10:00:07.000Z", out=7)])
        self.run_ingest(engines=["claude_code"])
        sub = self.one("SELECT * FROM sessions WHERE is_subagent=1")
        top = self.one("SELECT * FROM sessions WHERE is_subagent=0")
        self.assertEqual(sub["parent_session_id"], top["id"])
        self.assertEqual(sub["source_session_id"], "s1:agent-abc")
        self.assertEqual((top["output_tokens"], sub["output_tokens"]), (5, 7))  # not folded together
        self.assertEqual([r["id"] for r in queries.list_sessions(self.conn, subagents=None)].count(sub["id"]), 1)
        self.assertEqual(len(queries.list_sessions(self.conn)), 1)  # default = top-level only

    def test_corrupt_line_is_reported_not_fatal(self):
        p = os.path.join(self.claude, "proj", "s1.jsonl")
        _write(p, [_claude_user("2026-09-01T10:00:00.000Z"),
                   _claude_msg("m1", "claude-sonnet-5", "2026-09-01T10:00:05.000Z")],
               raw_tail='{"type": "assistant", "trunc')
        [ps] = claude_code.parse(_sf("claude_code", p))
        self.assertEqual(len(ps.events), 1)
        self.assertTrue(any("unreadable line" in w and "line 3" in w for w in ps.warnings))

    def test_same_message_in_two_files_counted_once_by_earliest_session(self):
        m = _claude_msg("shared", "claude-sonnet-5", "2026-09-01T10:00:05.000Z", out=9)
        _write(os.path.join(self.claude, "proj", "b-late.jsonl"),
               [_claude_user("2026-09-02T09:00:00.000Z"), m])
        _write(os.path.join(self.claude, "proj", "a-early.jsonl"),
               [_claude_user("2026-09-01T09:00:00.000Z"), m])
        rep = self.run_ingest(engines=["claude_code"])
        self.assertEqual(self.one("SELECT SUM(output_tokens) t FROM sessions")["t"], 9)
        owner = self.one("SELECT s.source_session_id sid FROM usage_events u JOIN sessions s ON s.id=u.session_id")
        self.assertEqual(owner["sid"], "a-early")
        self.assertEqual(rep.duplicate_events_skipped, 1)

    def test_empty_transcript_skipped(self):
        _write(os.path.join(self.claude, "proj", "empty.jsonl"), [{"type": "queue-operation", "timestamp": "2026-09-01T10:00:00.000Z"}])
        rep = self.run_ingest(engines=["claude_code"])
        c = rep.engines["claude_code"]
        self.assertEqual((c["discovered"], c["inserted"], c["skipped"]), (1, 0, 1))


class CodexAdapterTests(UsageDbCase):
    def _rollout(self, name="rollout-2026-09-01T10-00-00-cx1.jsonl", sid="cx1", events=None, meta=None, model="gpt-5.6-terra"):
        p = os.path.join(self.codex, "sessions", "2026", "09", "01", name)
        meta = dict({"id": sid, "timestamp": "2026-09-01T10:00:00.000Z", "cwd": "/work/proj", "source": "exec",
                     "model_provider": "openai", "cli_version": "0.1.0"}, **(meta or {}))
        recs = [{"timestamp": "2026-09-01T10:00:00.000Z", "type": "session_meta", "payload": meta},
                {"timestamp": "2026-09-01T10:00:01.000Z", "type": "turn_context", "payload": {"model": model}},
                {"timestamp": "2026-09-01T10:00:02.000Z", "type": "event_msg", "payload": {"type": "user_message"}}]
        recs += events or []
        _write(p, recs)
        return p

    @staticmethod
    def _tc(ts, last, total):
        return {"timestamp": ts, "type": "event_msg",
                "payload": {"type": "token_count", "info": {"last_token_usage": last, "total_token_usage": total}}}

    def test_cached_is_subset_of_input_and_duplicates_dropped(self):
        u1 = {"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 50, "reasoning_output_tokens": 20, "total_tokens": 1050}
        u2 = {"input_tokens": 500, "cached_input_tokens": 100, "output_tokens": 10, "reasoning_output_tokens": 0, "total_tokens": 510}
        t2 = {"input_tokens": 1500, "cached_input_tokens": 900, "output_tokens": 60, "total_tokens": 1560}
        p = self._rollout(events=[
            self._tc("2026-09-01T10:00:03.000Z", u1, u1),
            self._tc("2026-09-01T10:00:03.100Z", u1, u1),  # same event re-emitted
            self._tc("2026-09-01T10:00:04.000Z", u2, t2)])
        [ps] = codex.parse(_sf("codex", p))
        self.assertEqual(len(ps.events), 2)
        self.assertEqual((ps.events[0].input_tokens, ps.events[0].cache_read_tokens, ps.events[0].output_tokens), (200, 800, 50))
        self.assertEqual(ps.events[0].reasoning_tokens, 20)
        self.assertEqual(sum(e.input_tokens + e.cache_read_tokens + e.output_tokens for e in ps.events), 1560)
        self.assertTrue(ps.usage_complete)
        self.assertEqual(ps.warnings, [])

    def test_model_change_mid_session_attributes_per_event(self):
        u = {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5, "total_tokens": 15}
        p = self._rollout(events=[
            self._tc("2026-09-01T10:00:03.000Z", u, u),
            {"timestamp": "2026-09-01T10:00:04.000Z", "type": "turn_context", "payload": {"model": "gpt-5.6-sol"}},
            self._tc("2026-09-01T10:00:05.000Z", u, {**u, "total_tokens": 30, "input_tokens": 20, "output_tokens": 10})])
        [ps] = codex.parse(_sf("codex", p))
        self.assertEqual([e.model_id for e in ps.events], ["gpt-5.6-terra", "gpt-5.6-sol"])
        self.run_ingest(engines=["codex"])
        row = self.one("SELECT model_count, warning FROM sessions")
        self.assertEqual(row["model_count"], 2)
        self.assertIn("2 models", row["warning"])

    def test_non_monotonic_total_flags_unreconciled(self):
        a = {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 10, "total_tokens": 110}
        b = {"input_tokens": 50, "cached_input_tokens": 0, "output_tokens": 5, "total_tokens": 55}
        p = self._rollout(events=[self._tc("2026-09-01T10:00:03.000Z", a, {**a, "total_tokens": 1000}),
                                  self._tc("2026-09-01T10:00:04.000Z", b, {**b, "total_tokens": 400})])
        [ps] = codex.parse(_sf("codex", p))
        self.assertFalse(ps.usage_complete)
        self.assertTrue(any("not monotonic" in w for w in ps.warnings))
        self.assertEqual(len(ps.events), 2)

    def test_subagent_and_archived(self):
        p = self._rollout(sid="cx2", meta={"source": {"subagent": {"thread_spawn": {"parent_thread_id": "cx1"}}}})
        [ps] = codex.parse(_sf("codex", p, archived=True))
        self.assertTrue(ps.is_subagent)
        self.assertEqual(ps.parent_source_session_id, "cx1")
        self.assertEqual(ps.status, "archived")

    def test_no_usage_events_is_unknown_not_zero_cost(self):
        self._rollout()
        self.add_rate("gpt-5-6-terra", 2.5, 0.25, None, 15)
        self.run_ingest(engines=["codex"])
        row = self.one("SELECT cost_usd, cost_complete FROM session_costs")
        self.assertIsNone(row["cost_usd"])
        self.assertEqual(self.one("SELECT usage_complete FROM sessions")["usage_complete"], 0)


class KimiAdapterTests(UsageDbCase):
    def _session(self, sid="session_k1", agent="main", records=None, state=None):
        sdir = os.path.join(self.kimi, "sessions", "wd_proj_abc", sid)
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, "state.json"), "w") as fh:
            json.dump(dict({"id": sid, "cwd": "/work/proj", "archived": False, "createdAt": 1788000000000,
                            "updatedAt": 1788999999999}, **(state or {})), fh)
        recs = [{"type": "metadata", "protocol_version": "1.5", "created_at": 1788000000000}] + (records or [])
        p = os.path.join(sdir, "agents", agent, "wire.jsonl")
        _write(p, recs)
        return p

    @staticmethod
    def _usage(t, scope="turn", model="kimi-code/k3", **u):
        base = {"inputOther": 100, "output": 10, "inputCacheRead": 1000, "inputCacheCreation": 0}
        return {"type": "usage.record", "model": model, "usage": dict(base, **u), "usageScope": scope, "time": t}

    def test_usage_records_counted_step_end_not_double_counted(self):
        p = self._session(records=[
            {"type": "turn.prompt", "input": "x", "origin": {"kind": "user"}, "time": 1788000001000},
            {"type": "context.append_loop_event", "event": {"type": "tool.call"}, "time": 1788000002000},
            {"type": "context.append_loop_event", "event": {"type": "step.end", "usage": {"inputOther": 100}}, "time": 1788000003000},
            self._usage(1788000003000),
            self._usage(1788000010000, scope="session", inputOther=5000),
            {"type": "context.apply_compaction", "time": 1788000011000}])
        [ps] = kimi.parse(_sf("kimi", p))
        self.assertEqual(len(ps.events), 2)
        self.assertEqual([e.scope for e in ps.events], ["call", "session"])
        self.assertEqual((ps.user_message_count, ps.assistant_message_count, ps.tool_call_count, ps.compaction_count), (1, 1, 1, 1))
        self.assertEqual(ps.source_format_version, "1.5")
        # state.updatedAt (bulk-touched by housekeeping) must not become last_activity_at
        self.assertEqual(ps.last_activity_at, "2026-08-29T10:40:11.000Z")

    def test_subagent_and_missing_transcript(self):
        self._session(records=[self._usage(1788000003000)])
        self._session(agent="agent-1", records=[self._usage(1788000004000)])
        os.makedirs(os.path.join(self.kimi, "sessions", "wd_proj_abc", "session_empty", "agents"))
        rep = self.run_ingest(engines=["kimi"])
        c = rep.engines["kimi"]
        self.assertEqual((c["inserted"], c["skipped"]), (2, 1))
        sub = self.one("SELECT * FROM sessions WHERE is_subagent=1")
        self.assertEqual(sub["parent_source_session_id"], "session_k1")
        self.assertIsNotNone(sub["parent_session_id"])


class IngestBehaviourTests(UsageDbCase):
    def _seed(self):
        _write(os.path.join(self.claude, "proj", "s1.jsonl"),
               [_claude_user("2026-09-01T10:00:00.000Z"),
                _claude_msg("m1", "claude-sonnet-5", "2026-09-01T10:00:05.000Z", inp=100, cr=900, cc=0, out=50),
                _claude_msg("m2", "claude-opus-5", "2026-09-01T10:05:05.000Z", inp=200, cr=800, cc=100, out=60)])
        KimiAdapterTests._session(self, records=[KimiAdapterTests._usage(1788000003000)])
        _ = CodexAdapterTests._rollout(self, events=[CodexAdapterTests._tc(
            "2026-09-01T10:00:03.000Z",
            {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 5, "total_tokens": 105},
            {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 5, "total_tokens": 105})])

    def _dump(self):
        rows = self.conn.execute(
            "SELECT engine, source_session_id, model_id, model_count, message_count, input_tokens, "
            "cache_read_input_tokens, cache_creation_input_tokens, output_tokens, total_tokens, "
            "usage_event_count, started_at, last_activity_at, warning FROM sessions ORDER BY 1, 2").fetchall()
        return [tuple(r) for r in rows]

    def test_idempotent_then_incremental_update(self):
        self._seed()
        first = self.run_ingest()
        snap = self._dump()
        self.assertEqual(sum(c["inserted"] for c in first.engines.values()), 3)
        second = self.run_ingest()
        self.assertEqual(sum(c["inserted"] + c["updated"] for c in second.engines.values()), 0)
        self.assertEqual(sum(c["unchanged"] for c in second.engines.values()), 3)
        self.assertEqual(self._dump(), snap)
        self.assertEqual(second.events_added, 0)
        # append a new message: only that session changes, totals grow once
        with open(os.path.join(self.claude, "proj", "s1.jsonl"), "a") as fh:
            fh.write(json.dumps(_claude_msg("m3", "claude-opus-5", "2026-09-01T10:09:00.000Z", inp=1, cr=1, cc=0, out=7)) + "\n")
        third = self.run_ingest()
        self.assertEqual((third.engines["claude_code"]["updated"], third.engines["codex"]["unchanged"]), (1, 1))
        self.assertEqual(self.one("SELECT output_tokens o, usage_event_count n FROM sessions WHERE engine='claude_code'")["o"], 117)
        self.assertEqual(self.one("SELECT COUNT(*) n FROM usage_events WHERE engine='claude_code'")["n"], 3)

    def test_full_rebuild_matches_incremental(self):
        self._seed()
        self.run_ingest()
        with open(os.path.join(self.claude, "proj", "s1.jsonl"), "a") as fh:
            fh.write(json.dumps(_claude_msg("m3", "claude-opus-5", "2026-09-01T10:09:00.000Z", out=7)) + "\n")
        self.run_ingest()
        incremental = self._dump()
        rep = self.run_ingest(full_rebuild=True)
        self.assertEqual(self._dump(), incremental)
        self.assertEqual(sum(c["unchanged"] for c in rep.engines.values()), 0)

    def test_dry_run_writes_nothing(self):
        self._seed()
        rep = self.run_ingest(dry_run=True)
        self.assertEqual(sum(c["inserted"] for c in rep.engines.values()), 3)
        self.assertEqual(self.one("SELECT COUNT(*) n FROM sessions")["n"], 0)
        self.assertEqual(self.one("SELECT COUNT(*) n FROM ingest_files")["n"], 0)

    def test_engine_filter(self):
        self._seed()
        rep = self.run_ingest(engines=["kimi"])
        self.assertEqual(list(rep.engines), ["kimi"])
        self.assertEqual(self.one("SELECT COUNT(*) n FROM sessions WHERE engine!='kimi'")["n"], 0)

    def test_rotated_file_does_not_duplicate(self):
        self._seed()
        self.run_ingest()
        before = self.one("SELECT SUM(total_tokens) t, COUNT(*) n FROM sessions")
        old = os.path.join(self.claude, "proj", "s1.jsonl")
        new = os.path.join(self.claude, "proj-moved", "s1.jsonl")
        os.makedirs(os.path.dirname(new))
        os.rename(old, new)
        rep = self.run_ingest()
        after = self.one("SELECT SUM(total_tokens) t, COUNT(*) n FROM sessions")
        self.assertEqual((before["t"], before["n"]), (after["t"], after["n"]))
        self.assertEqual(rep.missing_source_files["claude_code"], 1)
        self.assertEqual(self.one("SELECT source_path p FROM sessions WHERE engine='claude_code'")["p"], new)

    def test_one_bad_file_does_not_stop_the_run(self):
        self._seed()
        real = claude_code.parse

        def boom(sf):
            if sf.path.endswith("bad.jsonl"):
                raise ValueError("cannot decode")
            return real(sf)
        _write(os.path.join(self.claude, "proj", "bad.jsonl"), [_claude_user("2026-09-01T10:00:00.000Z")])
        claude_code.parse = boom
        self.addCleanup(setattr, claude_code, "parse", real)
        rep = self.run_ingest()
        self.assertEqual(rep.engines["claude_code"]["failed"], 1)
        self.assertEqual(rep.engines["claude_code"]["inserted"], 1)
        self.assertTrue(any(d[1] == "failed" and "cannot decode" in d[3] for d in rep.details))
        self.assertEqual(self.one("SELECT status s FROM ingest_files WHERE path LIKE '%bad.jsonl'")["s"], "error")

    def test_missing_root_reported(self):
        rep = self.run_ingest(engines=["kimi"])
        self.assertTrue(any(d[1] == "missing_root" for d in rep.details))


class CostTests(UsageDbCase):
    def _two_model_session(self):
        _write(os.path.join(self.claude, "proj", "s1.jsonl"),
               [_claude_user("2026-09-01T10:00:00.000Z"),
                _claude_msg("m1", "claude-sonnet-5", "2026-09-01T10:00:05.000Z", inp=1_000_000, cr=2_000_000, cc=0, out=100_000),
                _claude_msg("m2", "claude-opus-5", "2026-09-01T11:00:05.000Z", inp=0, cr=0, cc=1_000_000, cc1h=400_000, out=0)])
        self.run_ingest(engines=["claude_code"])

    def test_priced_per_model_with_cache_split(self):
        self._two_model_session()
        self.add_rate("claude-sonnet-5", 2.0, 0.2, 2.5, 10.0)
        self.add_rate("claude-opus-5", 5.0, 0.5, 6.25, 25.0, cw1h=10.0)
        row = self.one("SELECT * FROM session_costs")
        # sonnet: 1M*2 + 2M*0.2 + 0.1M*10 = 3.4 ; opus: 0.6M*6.25 + 0.4M*10 = 7.75
        self.assertAlmostEqual(row["cost_usd"], 3.4 + 7.75, places=6)
        self.assertEqual(row["cost_complete"], 1)
        self.assertAlmostEqual(row["cache_read_savings_usd"], 2_000_000 * (2.0 - 0.2) / 1e6 + 0.0, places=6)

    def test_unknown_model_is_unknown_not_zero(self):
        self._two_model_session()
        self.add_rate("claude-sonnet-5", 2.0, 0.2, 2.5, 10.0)  # no opus rate
        row = self.one("SELECT * FROM session_costs")
        self.assertIsNone(row["cost_usd"])
        self.assertEqual(row["cost_complete"], 0)
        self.assertEqual(row["unpriced_event_count"], 1)
        self.assertAlmostEqual(row["cost_usd_priced"], 3.4, places=6)
        self.assertIsNone(self.one("SELECT cost_per_message_usd c FROM session_costs")["c"])

    def test_missing_rate_component_is_unknown(self):
        self._two_model_session()
        self.add_rate("claude-sonnet-5", 2.0, None, 2.5, 10.0)  # no cache-read rate but events read cache
        self.add_rate("claude-opus-5", 5.0, 0.5, 6.25, 25.0)
        self.assertIsNone(self.one("SELECT cost_usd c FROM session_costs")["c"])

    def test_rate_effective_dates(self):
        self._two_model_session()
        self.add_rate("claude-sonnet-5", 100.0, 100.0, 100.0, 100.0, eff="2026-01-01", to="2026-08-01")
        self.add_rate("claude-sonnet-5", 2.0, 0.2, 2.5, 10.0, eff="2026-08-01")
        self.add_rate("claude-opus-5", 5.0, 0.5, 6.25, 25.0, cw1h=10.0)
        self.assertAlmostEqual(self.one("SELECT cost_usd c FROM session_costs")["c"], 3.4 + 7.75, places=6)

    def test_period_rollups_and_summary(self):
        self._two_model_session()
        self.add_rate("claude-sonnet-5", 2.0, 0.2, 2.5, 10.0)
        self.add_rate("claude-opus-5", 5.0, 0.5, 6.25, 25.0, cw1h=10.0)
        for by, key in (("day", "2026-09-01"), ("month", "2026-09"), ("week", "2026-08-31")):
            rows = queries.summarize(self.conn, by)
            self.assertEqual([r["period"] for r in rows], [key], by)
            self.assertAlmostEqual(rows[0]["cost_usd_priced"], 11.15, places=6)
        self.assertEqual(len(queries.summarize(self.conn, "engine")), 2)

    def test_run_rate_and_break_even_keep_fees_separate(self):
        self._two_model_session()
        self.add_rate("claude-sonnet-5", 2.0, 0.2, 2.5, 10.0)
        self.add_rate("claude-opus-5", 5.0, 0.5, 6.25, 25.0, cw1h=10.0)
        rr = queries.run_rate(self.conn, "claude_code", as_of="2026-09-10T00:00:00Z")[0]
        self.assertAlmostEqual(rr["trailing_30d_usd"], 11.15, places=2)
        self.assertAlmostEqual(rr["month_projected_usd"], 11.15 / 9.0 * 30, places=1)
        be = queries.break_even(self.conn, 200.0, as_of="2026-09-10T00:00:00Z")
        self.assertIn("no active subscription plan", be["warning"])
        self.assertIsNone(be["current_subscription_fees_usd"])
        self.conn.execute("INSERT INTO subscription_plans (name, engine, monthly_fee) VALUES ('p','claude_code',100)")
        be = queries.break_even(self.conn, 200.0, as_of="2026-09-10T00:00:00Z")
        self.assertEqual(be["current_subscription_fees_usd"], 100)
        self.assertAlmostEqual(be["api_equivalent_per_current_subscription_dollar"], 0.11, places=2)

    def test_engine_with_no_recent_calls_reports_data_backed_zero(self):
        self._two_model_session()
        rr = queries.run_rate(self.conn, "claude_code", as_of="2027-01-01T00:00:00Z")[0]
        self.assertEqual((rr["trailing_30d_calls"], rr["trailing_30d_usd"]), (0, 0.0))

    def test_packaged_rates_load_and_are_idempotent(self):
        n = pricing.load_rates(self.conn)
        again = pricing.load_rates(self.conn)
        self.assertEqual(n, again)
        self.assertEqual(self.one("SELECT COUNT(*) n FROM price_rates")["n"], n)

    def test_list_sessions_order_whitelist(self):
        with self.assertRaises(ValueError):
            queries.list_sessions(self.conn, order="started_at; DROP TABLE sessions")


if __name__ == "__main__":
    unittest.main()
