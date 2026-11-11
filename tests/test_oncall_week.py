"""Weekly counts use only isolated retained records; no live services."""
from datetime import datetime
import json
import os
import time
from types import SimpleNamespace

import pytest

from ccc_server.oncall import api, alerts, store as st, week


def ts(s):
    return datetime.fromisoformat(s).timestamp()


@pytest.fixture
def eng(tmp_path):
    store = st.Store(tmp_path)
    tickets = []
    cfg = st.default_config()
    cfg['queue'] = 'TEST'
    obj = SimpleNamespace(store=store, config=lambda: cfg, clock=lambda: ts('2026-11-11T00:00:00+00:00'),
                          wt=SimpleNamespace(ls=lambda *a, **kw: tickets), tickets=tickets, cfg=cfg)
    yield obj
    store.close()


def report(eng, n, at, **kwargs):
    r, _ = eng.store.report_upsert('email', str(n), title='Private title', text='Private body',
                                  reporter_address='reporter@example.test', reporter_channel='mail', now=at, **kwargs)
    return r


def test_every_count_and_privacy(eng):
    at = eng.clock()-10000
    refs = ['TEST-1', 'TEST-2', 'TEST-3']
    for n, ref in enumerate(refs):
        r = report(eng, n, at)
        eng.store.report_update(r['id'], ticket_ref=ref)
    eng.tickets.extend([
        dict(ref=refs[0], status='closed', closed_at=at+90, resolution={'commit':'test-commit'},
             history=[{'event':'auto_decision', 'at':at}, {'event':'auto_decision_reversed', 'at':at+1}]),
        dict(ref=refs[1], status='closed', closed_at=at+100, resolution={}), dict(ref=refs[2], status='open')])
    for n, (kind, answer) in enumerate([('report','go'), ('report','no'), ('send','yes'), ('send','new wording'), ('send','no')]):
        a, _ = eng.store.alert_upsert(str(n), kind=kind, title='Private', body='',
                                     meta={'message_id':n+1} if kind=='send' else {}, now=at)
        eng.store.alert_mark_sent(a['id'], now=at+10)
        eng.store.delivery_add(a['id'], 'mail', 'owner@example.test', st.Receipt(True), now=at+10)
        eng.store.alert_close(a['id'], 'answered', answer, now=at+30)
    eng.store.delivery_add(None, 'mail', 'reporter@example.test', st.Receipt(False), now=at)
    eng.store.customer_message_add(ref=refs[0], kind='ask', channel='mail', address='reporter@example.test',
                                    text='Private', state='sent', now=at)
    data = week.collect(eng)
    p = data['periods'][0]
    assert p['reports'] == dict(received=3, by_source={'email':3}, by_channel={'mail':3}, held=2, released=1, dropped=1)
    assert p['tickets'] == dict(filed=3, fixed=1, closed_without_fix=1, still_open=1, unknown=0)
    assert p['report_to_fix_seconds'] == dict(median=90, worst=90)
    assert p['owner_messages'] == dict(report=2, send=3)
    assert p['replies'] == dict(received=5, median_seconds=20)
    assert p['decisions'] == dict(made=1, undone=1)
    assert p['reporter_messages'] == dict(asked=1, approved=1, reworded=1, refused=1)
    assert p['failed_deliveries'] == 1
    assert 'example.test' not in json.dumps(data)
    detailed = week.collect(eng, detail=True)
    assert 'reporter@example.test' in json.dumps(detailed)
    for markdown in (False, True):
        assert 'example.test' not in week.render(detailed, markdown)
        assert 'Private' not in week.render(detailed, markdown)
    assert api.handle('GET', '/api/oncall/week', eng=eng)[0] == 200


@pytest.mark.parametrize('zone', ['UTC','America/Los_Angeles','Asia/Kolkata'])
def test_boundaries_and_time_zones(eng, monkeypatch, zone):
    old = os.environ.get('TZ')
    monkeypatch.setenv('TZ', zone)
    time.tzset()
    try:
        spans = week.windows(eng.clock(), weeks=2)
        boundary = spans[0][0]
        for n, at in enumerate((spans[1][0]-1, spans[1][0], boundary-1, boundary, eng.clock())):
            report(eng,n,at)
        data = week.collect(eng)
        assert [p['reports']['received'] for p in data['periods']] == [1,2]
        explicit = week.windows(eng.clock(), '2026-11-01T00:00:00-07:00', 1)
        assert explicit[0][0] == ts('2026-11-01T07:00:00+00:00')
    finally:
        if old is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = old
        time.tzset()


def test_summary_off_empty_once_quiet_cap(eng):
    now = ts('2026-11-11T12:00:00')
    week.daily(eng, now)
    assert not eng.store.alerts()
    eng.cfg['daily_summary'] = {'enabled':True, 'time':'09:00'}
    week.daily(eng, now)
    assert not eng.store.alerts()
    eng.store.kv_set('summary_day', '')
    report(eng, 1, ts('2026-11-10T12:00:00'))
    week.daily(eng, now)
    week.daily(eng, now)
    rows = eng.store.alerts()
    assert len(rows) == 1
    assert len(alerts.render(rows[0])[1].splitlines()) <= 6
    sent = []
    chan = SimpleNamespace(id='mail', owner='owner@example.test', send=lambda m: sent.append(m) or st.Receipt(True))
    eng.cfg['channels'] = [{'id':'mail'}]
    eng.cfg['quiet_hours'] = {'start':'00:00','end':'23:59'}
    assert alerts.deliver(eng.cfg, eng.store, {'mail':chan}, now=now) == 0
    eng.cfg['quiet_hours'] = None
    eng.cfg['hourly_cap'] = 1
    eng.store.delivery_add(999,'mail','',st.Receipt(True),now=now-1)
    assert alerts.deliver(eng.cfg, eng.store, {'mail':chan}, now=now) == 0
    assert alerts.deliver(eng.cfg, eng.store, {'mail':chan}, now=now+3601) == 1
    assert len(sent) == 1


def test_perf_budget_old_store(eng):
    # The query must range-scan indexes, not walk historical rows or call WT per report.
    with eng.store._db:
        eng.store._db.executemany("INSERT INTO reports(source,fingerprint,title,created_at,updated_at) VALUES('test',?,'',1,1)",
                                 ((str(n),) for n in range(100000)))
    calls = []
    eng.wt.ls = lambda *a, **kw: calls.append(kw) or []
    report(eng,'recent',eng.clock()-1)
    start = time.monotonic()
    assert week.collect(eng)['periods'][0]['reports']['received'] == 1
    assert time.monotonic()-start < 1
    assert len(calls) == 1
    plan = eng.store._all('EXPLAIN QUERY PLAN SELECT * FROM reports WHERE created_at>=? AND created_at<? LIMIT 20001', (2, eng.clock()))
    assert 'reports_created' in str(plan)


def test_limits(eng):
    with pytest.raises(ValueError):
        week.collect(eng, weeks=53)
    eng.wt.ls = lambda *a, **kw: [{}] * (week.TICKET_LIMIT+1)
    with pytest.raises(ValueError, match='ticket limit'):
        week.collect(eng)


def test_fix_in_later_week(eng):
    at = eng.clock()-10*86400
    r = report(eng,1,at)
    eng.store.report_update(r['id'],ticket_ref='TEST-1')
    eng.tickets.append(dict(ref='TEST-1',status='closed',closed_at=eng.clock()-1,resolution={'commit':'test'}))
    periods = week.collect(eng)['periods']
    assert periods[0]['tickets']['filed'] == 0
    assert periods[0]['tickets']['fixed'] == 1
    assert periods[1]['tickets']['filed'] == 1
    assert periods[1]['tickets']['fixed'] == 0


def test_direct_and_reporter_deliveries(eng):
    at = eng.clock()-1
    eng.store.delivery_add(None,'mail','owner@example.test',st.Receipt(True),now=at)
    eng.store.delivery_add(None,'mail','reporter@example.test',st.Receipt(True),now=at,message_kind='reporter')
    assert week.collect(eng)['periods'][0]['owner_messages'] == {'direct':1}


def test_summary_does_not_feed_itself(eng):
    eng.cfg['daily_summary'] = {'enabled':True, 'time':'00:00'}
    at = ts('2026-11-10T12:00:00')
    a,_ = eng.store.alert_upsert('old',kind='summary',title='',body='',needs_reply=False,now=at)
    eng.store.delivery_add(a['id'],'mail','',st.Receipt(True),now=at)
    week.daily(eng,ts('2026-11-11T12:00:00'))
    assert len(eng.store.alerts()) == 1
    assert eng.store.alert(a['id'])['state'] == 'cleared'


def test_local_api_reads_when_preview_off(eng, monkeypatch):
    import server
    monkeypatch.setattr(server, '_feature_flag', lambda name: False)
    monkeypatch.setattr(server, '_oncall_engine', lambda: eng)
    captured = []
    handler = SimpleNamespace(client_address=('127.0.0.1',1234), headers={},
                              server=SimpleNamespace(server_address=('127.0.0.1', 8090)),
                              send_json=lambda data, code: captured.append((code,data)))
    server._oncall_request(handler,'GET','/api/oncall/week',{'weeks':'1'})
    assert captured[-1][0] == 200
    assert len(captured[-1][1]['periods']) == 1
    handler.client_address = ('203.0.113.1',1234)
    server._oncall_request(handler,'GET','/api/oncall/week')
    assert captured[-1][0] == 403
    handler.client_address = ('127.0.0.1',1234)
    handler.headers = {'Origin': 'https://example.test'}
    server._oncall_request(handler,'GET','/api/oncall/week')
    assert captured[-1][0] == 403


def test_cli_exports_ignore_detail(eng):
    from ccc_server.oncall import cli
    from urllib.parse import urlparse, parse_qs
    report(eng,1,eng.clock()-1)
    output = []
    def get(base, endpoint, **kwargs):
        query = {k:v[0] for k,v in parse_qs(urlparse(endpoint).query).items()}
        return api.handle('GET','/api/oncall/week',query,eng=eng)[1]
    for post in ('text','markdown'):
        assert cli.run(['week','--post',post,'--detail'],base='http://127.0.0.1',get=get,post=None,
                       io=cli.IO(out=output.append)) == 0
    assert 'example.test' not in '\n'.join(output)


def test_row_budget_refuses_partial_counts(eng, monkeypatch):
    monkeypatch.setattr(week,'ROW_LIMIT',2)
    for n in range(3):
        report(eng,n,eng.clock()-1)
    with pytest.raises(ValueError,match='row limit'):
        week.collect(eng)


def test_invalid_summary_time(eng):
    eng.cfg['daily_summary'] = {'enabled':True,'time':'25:00'}
    with pytest.raises(ValueError,match='Daily summary'):
        st.save_config(eng.store.root,eng.cfg)


def test_summary_off_cancels_pending(eng):
    a,_ = eng.store.alert_upsert('queued',kind='summary',title='',body='',needs_reply=False)
    week.daily(eng,eng.clock())
    assert eng.store.alert(a['id'])['state'] == 'cleared'


def test_approval_count_survives_failed_delivery(eng):
    a,_ = eng.store.alert_upsert('send',kind='send',title='',body='',meta={'message_id':1},now=eng.clock()-10)
    eng.store.alert_owner_action(a['id'],'reworded',now=eng.clock()-2)
    eng.store.delivery_add(None,'mail','',st.Receipt(False),now=eng.clock()-1,message_kind='reporter')
    p = week.collect(eng)['periods'][0]
    assert p['reporter_messages']['reworded'] == 1
    assert p['replies']['received'] == 0
    assert p['failed_deliveries'] == 1


def test_week_reads_in_plain_words(eng):
    at = eng.clock() - 10000
    r = report(eng, 1, at)
    eng.store.report_update(r['id'], ticket_ref='TEST-1')
    eng.tickets.append(dict(ref='TEST-1', status='closed', closed_at=at + 5400, resolution={'commit': 'test-commit'}))
    text = week.render(week.collect(eng))
    assert 'Tickets: 1 filed, 1 fixed, 0 closed without a fix, 0 still open' in text
    assert 'Report to fix: median 1 h 30 min, worst 1 h 30 min' in text
    assert 'median time to reply not recorded' in text
    assert [week.span(n) for n in (40, 600, 7200, 200000)] == ['40 s', '10 min', '2 h', '2 d 7 h']
