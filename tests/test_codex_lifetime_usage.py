"""Lifetime billing survives counter resets; provider output includes reasoning."""
import json
import pytest
import server


def record(i, cached, out, reasoning=0, *, ts='2026-09-01T00:00:00Z', last=None):
    totals = {'input_tokens':i,'cached_input_tokens':cached,'output_tokens':out,
              'reasoning_output_tokens':reasoning,'total_tokens':i+out}
    return {'type':'event_msg','timestamp':ts,'payload':{'type':'token_count','info':{
        'total_token_usage':totals,'last_token_usage':last or totals,'model_context_window':1000000}}}


def fixture_usage(tmp_path, monkeypatch, events):
    rollout = tmp_path/'rollout.jsonl'
    rollout.write_text('\n'.join(json.dumps(e) for e in [
        {'type':'turn_context','payload':{'model':'gpt-6-astra'}},*events])+'\n')
    monkeypatch.setattr(server,'_resolve_codex_rollout_path',lambda _:rollout)
    monkeypatch.setattr(server,'_codex_thread_row',lambda _:{})
    return server._extract_codex_usage('example-session')


def test_lifetime_accumulates_segments_after_counter_restart(tmp_path, monkeypatch):
    u = fixture_usage(tmp_path,monkeypatch,[record(1000000,800000,100000,20000),
        record(100000,80000,10000,2000,ts='2026-09-01T01:00:00Z')])
    assert u['total_input_tokens'] == 220000
    assert u['total_cache_read_tokens'] == 880000
    assert u['total_output_tokens'] == 110000
    assert u['cost_usd'] == pytest.approx(2.2+0.88+5.5)


def test_repeated_token_notifications_are_not_extra_model_calls(tmp_path, monkeypatch):
    event=record(1000000,800000,100000,20000)
    u=fixture_usage(tmp_path,monkeypatch,[event,event])
    assert len(u['turn_series']) == 1
    assert u['cost_usd'] == pytest.approx(2+0.8+5)


def test_throughput_uses_cumulative_delta_and_counts_reasoning_once(tmp_path, monkeypatch):
    first=record(1000000,800000,100000,20000)
    fixture_usage(tmp_path,monkeypatch,[first,first,record(1500000,1200000,150000,30000,
        ts='2026-09-01T01:00:00Z',last={'input_tokens':500000,'cached_input_tokens':400000,
        'output_tokens':50000,'reasoning_output_tokens':10000,'total_tokens':550000})])
    turns=server._throughput_codex_turns_from_file('example-session',model_hint='gpt-6-astra')
    assert len(turns) == 2
    assert sum(t['tokens_out'] for t in turns) == 150000
    assert sum(t['cost_usd'] for t in turns) == pytest.approx(3+1.2+7.5)


def test_last_usage_only_events_still_accumulate(tmp_path, monkeypatch):
    events=[record(1000,800,100),record(1000,800,100,ts='2026-09-01T01:00:00Z')]
    for e in events:del e['payload']['info']['total_token_usage']
    u=fixture_usage(tmp_path,monkeypatch,events)
    assert u['total_input_tokens']==400
    assert u['total_cache_read_tokens']==1600
    assert u['total_output_tokens']==200


def test_model_switch_prices_each_segment_at_its_own_rate(tmp_path, monkeypatch):
    u=fixture_usage(tmp_path,monkeypatch,[record(100000,80000,10000),
        {'type':'turn_context','payload':{'model':'gpt-5.6-sol'}},
        record(200000,160000,20000,ts='2026-09-01T01:00:00Z')])
    assert u['cost_usd'] == pytest.approx(0.78+0.44)


def test_old_codex_turn_cache_reparses_once_then_reuses_memory(tmp_path, monkeypatch):
    import ccc_server.usage_stats as stats
    rollout=tmp_path/'rollout.jsonl';rollout.write_text('{}\n')
    monkeypatch.setattr(stats,'_THROUGHPUT_TURN_CACHE',{})
    monkeypatch.setattr(stats,'_throughput_disk_get',lambda *args:[{'engine':'codex','cost_usd':99}])
    monkeypatch.setattr(stats,'_throughput_disk_put',lambda *args:None)
    calls=[]
    def extract():
        calls.append(True)
        return [{'engine':'codex','codex_usage_schema':3,'cost_usd':1}]
    assert stats._throughput_file_turns(rollout,extract)[0]['cost_usd']==1
    assert stats._throughput_file_turns(rollout,extract)[0]['cost_usd']==1
    assert len(calls)==1


def test_token_event_model_takes_precedence_without_context_switch(tmp_path, monkeypatch):
    first=record(100000,80000,10000)
    second=record(200000,160000,20000,ts='2026-09-01T01:00:00Z')
    second['payload']['info']['model']='gpt-5.6-sol'
    u=fixture_usage(tmp_path,monkeypatch,[first,second])
    assert u['model']=='gpt-5.6-sol'
    assert u['cost_usd']==pytest.approx(0.78+0.44)


def test_empty_status_between_cumulative_reports_does_not_reset_baseline(tmp_path, monkeypatch):
    u=fixture_usage(tmp_path,monkeypatch,[record(100000,80000,10000),record(0,0,0),
        record(200000,160000,20000,ts='2026-09-01T01:00:00Z')])
    assert u['total_input_tokens']==40000
    assert u['total_output_tokens']==20000
