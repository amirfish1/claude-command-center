"""Static export contracts, from isolated stored activity."""
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ccc_server.oncall import cli, store as st, week, week_visual as visual

PRIVATE = 'PRIVATE-reporter@example.test'


def seeded(root, *, empty=False, large=False):
    store = st.Store(root)
    now = datetime(2026,11,11,tzinfo=timezone.utc).timestamp()
    tickets = []
    cfg = st.default_config()
    cfg.update(queue='TEST', repo=PRIVATE)
    eng = SimpleNamespace(store=store,config=lambda:cfg,clock=lambda:now,
                          wt=SimpleNamespace(ls=lambda *a,**kw:tickets))
    try:
        for n in range(4):
            at = now-(n*7+2)*86400
            count = 0 if empty else (12345 if large and n==0 else (23,31,18,12)[n])
            for i in range(count):
                report,_ = store.report_upsert('source:'+PRIVATE,f'{n}-{i}',title=PRIVATE,text=PRIVATE,
                      reporter_address=PRIVATE,reporter_channel=PRIVATE,now=at)
                if i < min(count,8):
                    ref = f'TEST-{n*8+i}'
                    store.report_update(report['id'],ticket_ref=ref)
                    tickets.append(dict(ref=ref,status='closed',closed_at=at+3600*(i+1),
                        resolution={'commit':'test'},history=[{'event':'auto_decision','at':at+100}]))
            if count:
                a,_ = store.alert_upsert(str(n),kind='decision',title=PRIVATE,body=PRIVATE,now=at)
                for channel in ('one','two'):
                    store.delivery_add(a['id'],channel,PRIVATE,st.Receipt(True),now=at+10)
                store.alert_mark_sent(a['id'],now=at+10)
                store.alert_close(a['id'],'answered',PRIVATE,now=at+130)
        return week.collect(eng,weeks=8,detail=True)
    finally:
        store.close()


@pytest.fixture
def data(tmp_path):
    return seeded(tmp_path)


def test_numbers_from_month(data):
    p = visual.public_periods(data)[0]
    assert p['counts'] == [23,8,1,8]
    assert p['times'] == [16200,120]
    svg = visual.card(data)
    assert 'Bugs reported: 23' in svg
    assert 'Fixed by agents: 8' in svg
    assert 'Questions that reached you: 1' in svg
    assert '4 h 30 min' in svg and '2 min' in svg
    assert '8 weeks' in svg
    assert visual.page(data).count('<section>') == 8


@pytest.mark.parametrize('theme',['light','dark'])
@pytest.mark.parametrize('square',[False,True])
def test_privacy_and_sizes(data,theme,square):
    svg = visual.card(data,theme=theme,square=square)
    html = visual.page(data,theme=theme)
    for output in (svg,html):
        assert PRIVATE not in output
        assert 'TEST-' not in output
        assert '<script' not in output and '@import' not in output
        assert 'url(' not in output
    assert f'width="{1080 if square else 1200}" height="{1080 if square else 675}"' in svg


def test_empty_week(tmp_path):
    data = seeded(tmp_path,empty=True)
    assert visual.public_periods(data)[0]['counts'] == [0,0,0,0]
    assert 'not recorded' in visual.card(data)
    assert 'Bugs reported ·' not in visual.card(data)


@pytest.mark.parametrize('bad',['limits','unknown','missing','private_count'])
def test_refuse_bad_counts(data,tmp_path,bad):
    if bad == 'limits': data['limits'] = ['missing queue']
    elif bad == 'unknown': data['periods'][0]['tickets']['unknown'] = 1
    elif bad == 'missing': del data['periods'][0]['owner_questions_sent']
    else: data['periods'][0]['reports']['received'] = PRIVATE
    for suffix in ('svg','html'):
        target = tmp_path / ('export.'+suffix)
        target.write_text('previous')
        with pytest.raises(ValueError,match='Export unavailable'):
            visual.write_export(data,target,html=suffix=='html')
        assert target.read_text() == 'previous'


def test_cli_requests_eight_private_weeks(data,tmp_path):
    calls=[]
    def get(base,path,**kw):
        calls.append(path)
        return data
    target = tmp_path / 'card.svg'
    assert cli.run(['week','--detail','--card',str(target),'--square','--theme','dark'],
                   base='http://127.0.0.1',get=get,post=None,io=cli.IO(out=lambda text:None)) == 0
    assert 'weeks=8' in calls[0] and 'detail=0' in calls[0]
    assert PRIVATE not in target.read_text()


def test_png_missing_browser_leaves_destination(data,tmp_path,monkeypatch):
    def missing(*a,**kw): raise OSError('missing node')
    monkeypatch.setattr(visual.subprocess,'Popen',missing)
    target = tmp_path / 'card.png'
    target.write_bytes(b'previous')
    with pytest.raises(ValueError,match='Export .svg instead'):
        visual.write_export(data,target)
    assert target.read_bytes() == b'previous'


def test_window_adapter_bounds_and_older_fallback():
    from ccc_server.oncall.wt import WT, WTError
    calls=[]
    class Queue(WT):
        def _call(self,*args,**kw):
            calls.append(args)
            return 0,json.dumps({'items':[], 'complete':True}),''
    wt = Queue(argv=['wt'])
    assert wt.week_window('TEST',100,200,{'TEST-1'}) == []
    assert '--closed-since' in calls[0] and '--event-since' in calls[0] and '--ref' in calls[0]
    wt._call = lambda *a,**k:(0,json.dumps({'items':[], 'complete':False}),'')
    with pytest.raises(WTError,match='incomplete'):
        wt.week_window('TEST',100,200,set())
    def old(*a,**kw):
        return (2,'','unrecognized arguments: --closed-since') if '--closed-since' in a else (0,'[]','')
    wt._call=old
    assert wt.week_window('TEST',100,200,set()) == []


def test_real_window_cli_isolated(tmp_path):
    import os
    import shutil
    import subprocess
    from ccc_server.oncall.wt import WT
    executable = shutil.which('wt')
    if not executable:
        pytest.skip('WatchTower not installed')
    env = {key:os.environ[key] for key in ('PATH','LANG','LC_ALL') if key in os.environ}
    env.update(HOME=str(tmp_path),XDG_DATA_HOME=str(tmp_path/'data'),XDG_CONFIG_HOME=str(tmp_path/'config'),
               WATCHTOWER_STORE=str(tmp_path/'queues.json'),WT_NO_AUTOSTART='1',WT_NO_SELF_UPDATE='1',
               WATCHTOWER_DELEGATE_URL='off',CCC_STATE_DIR=str(tmp_path/'state'))
    help_text = subprocess.run([executable,'ls','--help'],env=env,capture_output=True,text=True,timeout=10).stdout
    if '--closed-since' not in help_text:
        pytest.skip('WatchTower window selectors not installed')
    items = [dict(ref=f'TEST-{i}',seq=i,project='TEST',status='closed',closed_at='2026-01-01T00:00:00Z',
                  history=[],title=PRIVATE) for i in range(1,101)]
    items += [dict(ref='TEST-101',seq=101,project='TEST',status='closed',closed_at='2026-11-05T12:00:00Z',history=[]),
              dict(ref='TEST-102',seq=102,project='TEST',status='open',history=[])]
    (tmp_path/'queues.json').write_text(json.dumps({'counter':102,'items':items}))
    start = datetime(2026,11,4,tzinfo=timezone.utc).timestamp()
    end = datetime(2026,11,11,tzinfo=timezone.utc).timestamp()
    rows = WT(argv=[executable],env=env,dispatch=False).week_window('TEST',start,end,{'TEST-102'},limit=3)
    assert {r['ref'] for r in rows} == {'TEST-101','TEST-102'}


def test_png_timeout_cleans_process_group(data,tmp_path,monkeypatch):
    import subprocess
    if visual.os.name != 'posix':
        pytest.skip('POSIX process group cleanup')
    killed=[]
    class Stalled:
        pid=12345
        n=0
        def communicate(self,*a,**kw):
            self.n+=1
            if self.n==1: raise subprocess.TimeoutExpired('node',45)
            return b'',b''
    proc=Stalled()
    monkeypatch.setattr(visual.subprocess,'Popen',lambda *a,**kw:proc)
    monkeypatch.setattr(visual.os,'killpg',lambda pid,sig:killed.append(pid))
    with pytest.raises(ValueError,match='Export .svg instead'):
        visual.write_export(data,tmp_path/'card.png')
    assert killed == [12345] and proc.n == 2
    assert not (tmp_path/'card.png').exists()
