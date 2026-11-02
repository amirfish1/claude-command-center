"""Capability metadata must neither inspect secrets nor probe runtime services."""
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
import server
import federation
from ccc_server import cloud_relay, free_router, phone_access, realtime_voice
from ccc_server.settings_capabilities import inventory, snapshot


def test_presence_reads_no_contents_and_has_no_paths_in_result(tmp_path, monkeypatch):
    marker=tmp_path/'example-config.json'
    marker.write_text('not-a-secret')
    monkeypatch.setattr(Path,'read_text',lambda *args,**kwargs:pytest.fail('No file contents may be read'))
    result=inventory({'voice':[marker],'remote':[tmp_path/'absent']},{'voice':'off','free':'invalid'})
    assert result['capabilities']['voice']=={'configured':True,'runtime':'off'}
    assert result['capabilities']['remote']=={'configured':False,'runtime':'unknown'}
    assert result['capabilities']['free']['runtime']=='unknown'
    assert str(tmp_path) not in json.dumps(result)
    assert 'not-a-secret' not in json.dumps(result)


def test_presence_error_is_unknown_but_does_not_erase_confirmed_setup(tmp_path,monkeypatch):
    native=Path.exists
    def exists(path):
        if path.name=='denied':raise PermissionError('fixture')
        return native(path)
    (tmp_path/'saved').touch()
    monkeypatch.setattr(Path,'exists',exists)
    result=inventory({'voice':[tmp_path/'denied'],'remote':[tmp_path/'saved',tmp_path/'denied']})['capabilities']
    assert result['voice']['configured'] is None
    assert result['remote']['configured'] is True


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(realtime_voice,'VOICE_CONFIG_FILE',tmp_path/'voice.json')
    monkeypatch.setattr(realtime_voice,'_SESSION',None)
    monkeypatch.setattr(free_router,'state_path',lambda:tmp_path/'router.json')
    monkeypatch.setattr(free_router,'config_path',lambda:tmp_path/'router-config.json')
    monkeypatch.setattr(free_router,'install_dir',lambda:tmp_path/'router-install')
    monkeypatch.setattr(free_router,'_STATUS_CACHE',{'data':None,'at':0})
    monkeypatch.setattr(phone_access,'state_file',lambda:tmp_path/'phone.json')
    monkeypatch.setattr(federation,'_peers_file',lambda:tmp_path/'peers.json')
    monkeypatch.setattr(cloud_relay,'_default_state_dir',lambda:str(tmp_path/'cloud'))
    monkeypatch.setattr(server,'NETWORK_CONFIG_FILE',tmp_path/'network.json')
    monkeypatch.setattr(server,'CAR_MODE_CONFIG_FILE',tmp_path/'operator.json')
    monkeypatch.setattr(server,'_CAR_MODE_DIR',tmp_path/'operator')
    monkeypatch.setattr(server,'_CAR_MODE',{})
    monkeypatch.setattr(server,'RUNTIME_NETWORK_INFO',{'bind_host':'127.0.0.1'})
    def forbidden(*args,**kwargs):pytest.fail('No credentials, network or process probes in capability inventory')
    for owner,name in [(server,'byok_get_key'),(server,'_car_mode_snapshot'),(server,'_detect_tailnet_origins'),(free_router,'status'),(phone_access,'overview'),(realtime_voice,'voice_status')]:monkeypatch.setattr(owner,name,forbidden)
    monkeypatch.setattr(server.subprocess,'Popen',forbidden)
    monkeypatch.setattr(server.subprocess,'run',forbidden)
    return tmp_path


def test_runtime_facts_and_unknowns_are_separate_from_configuration(isolated,monkeypatch):
    rows=snapshot(server)['capabilities']
    assert rows['voice']=={'configured':False,'runtime':'off'}
    assert rows['remote']=={'configured':False,'runtime':'unknown'}
    assert rows['operator']['runtime']=='unavailable'
    (isolated/'phone.json').write_text('{"fixture":"not-a-secret"}')
    (isolated/'router.json').touch()
    (isolated/'voice.json').touch()
    monkeypatch.setattr(realtime_voice,'_SESSION',SimpleNamespace(state='open'))
    monkeypatch.setattr(server,'RUNTIME_NETWORK_INFO',{'bind_host':'0.0.0.0'})
    monkeypatch.setattr(free_router,'_STATUS_CACHE',{'data':{'running':True},'at':time.time()})
    rows=snapshot(server)['capabilities']
    assert rows['voice']=={'configured':True,'runtime':'on'}
    assert rows['remote']=={'configured':True,'runtime':'on'}
    assert rows['free']=={'configured':True,'runtime':'on'}
    monkeypatch.setattr(free_router,'_STATUS_CACHE',{'data':{'running':True},'at':0})
    assert snapshot(server)['capabilities']['free']['runtime']=='unknown'


def test_tracked_operator_retains_configuration_without_claiming_process_liveness(isolated,monkeypatch):
    monkeypatch.setattr(server,'_CAR_MODE',{'pid':12345})
    assert snapshot(server)['capabilities']['operator']=={'configured':True,'runtime':'unknown'}


def test_real_get_contract_is_read_only_and_rejects_no_configuration(isolated):
    class Handler(server.CommandCenterHandler):
        def log_message(self,*args):pass
        def _phone_pin_blocked(self,path):return False
        def _check_proxy_secret(self):return True
    httpd=server.http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    before=list(isolated.iterdir())
    try:
        with urllib.request.urlopen('http://127.0.0.1:'+str(httpd.server_address[1])+'/api/settings/capabilities',timeout=5) as response:
            assert response.status==200
            result=json.loads(response.read())
        assert set(result['capabilities'])=={'voice','remote','free','operator'}
        assert list(isolated.iterdir())==before
    finally:
        httpd.shutdown();httpd.server_close();thread.join(timeout=5)

@pytest.mark.parametrize('entry',['peers.json','cloud/config.json'])
def test_existing_peer_or_cloud_setup_keeps_remote_manager_reachable_without_reading_it(isolated,entry,monkeypatch):
    location=isolated/entry
    location.parent.mkdir(exist_ok=True)
    location.write_text('{"example":"not-a-secret"}')
    monkeypatch.setattr(Path,'read_text',lambda *args,**kwargs:pytest.fail('No stored content may be read'))
    assert snapshot(server)['capabilities']['remote']=={'configured':True,'runtime':'unknown'}
