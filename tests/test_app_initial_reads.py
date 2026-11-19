"""Initial reads are bounded, explicit, current, and use canonical payloads."""
import json
from pathlib import Path

import pytest
from ccc_server import app_initial_reads as initial, app_data


@pytest.fixture
def app(tmp_path):
    folder = tmp_path / 'demo'
    folder.mkdir()
    (folder/'data').mkdir()
    return tmp_path,folder


def configure(folder, files=(), data=()):
    (folder/'app.json').write_text(json.dumps({'startup_reads':{'files':list(files),'data':list(data)}}))


def test_exact_file_and_data_values_and_missing_key_match_existing_read(app):
    base,folder = app
    configure(folder,['items.json'],['choice','missing'])
    (folder/'items.json').write_text('{"title":"café","items":[1,2]}')
    app_data.write({'app':'demo','key':'choice','value':{'selected':True}},base)
    result=initial.read('demo',base)
    assert result['/app-static/demo/items.json'] == (folder/'items.json').read_text()
    for key in ['choice','missing']:
        assert json.loads(result['/api/apps/data?app=demo&key='+key]) == app_data.read('demo',key,base)[0]
    # A subsequent page gets current data, with no TTL or stale publication.
    (folder/'items.json').write_text('{"items":[3]}')
    assert initial.read('demo',base)['/app-static/demo/items.json'] == '{"items":[3]}'


def test_only_declared_local_bounded_json_is_supplied(app,tmp_path):
    base,folder=app
    outside=tmp_path/'outside.json';outside.write_text('{"outside":true}')
    (folder/'link.json').symlink_to(outside)
    (folder/'bad.json').write_text('broken')
    (folder/'large.json').write_text(json.dumps('x'*initial.MAX_FILE_BYTES))
    (folder/'ok.json').write_text('{}')
    (folder/'undeclared.json').write_text('{}')
    (folder/'data'/'bad.json').write_text('[]')
    (folder/'data'/'large.json').write_text(json.dumps({'value':'x'*initial.MAX_FILE_BYTES}))
    (folder/'data'/'link.json').symlink_to(outside)
    configure(folder,['../outside.json','link.json','bad.json','large.json','ok.json'],
              ['bad','large','link','../outside'])
    assert initial.read('demo',base) == {'/app-static/demo/ok.json':'{}'}
    assert initial.read('../demo',base) == {}


def test_linked_app_outside_apps_root_is_ineligible(app,tmp_path):
    base,folder=app
    other=tmp_path.parent/(tmp_path.name+'-other')
    other.mkdir()
    (other/'app.json').write_text('{"startup_reads":{"files":["items.json"]}}')
    (other/'items.json').write_text('{}')
    (base/'linked').symlink_to(other,target_is_directory=True)
    assert initial.read('linked',base) == {}


def test_local_app_alias_and_local_file_link_keep_current_values(app):
    base, folder = app
    configure(folder, ['items.json'])
    target = folder / 'current.json'
    target.write_text('{"count":1}')
    (folder / 'items.json').symlink_to(target)
    (base / 'alias').symlink_to(folder, target_is_directory=True)
    assert initial.read('alias', base) == {'/app-static/alias/items.json':'{"count":1}'}
    target.write_text('{"count":2}')
    assert initial.read('alias', base) == {'/app-static/alias/items.json':'{"count":2}'}


def test_declared_nonregular_files_are_not_opened(app):
    import os
    base, folder = app
    configure(folder, ['directory.json', 'pipe.json', 'valid.json'])
    (folder / 'directory.json').mkdir()
    if hasattr(os, 'mkfifo'):
        os.mkfifo(folder / 'pipe.json')
    (folder / 'valid.json').write_text('{}')
    assert initial.read('demo', base) == {'/app-static/demo/valid.json':'{}'}


def test_manifest_and_combined_size_are_bounded(app,monkeypatch):
    base,folder=app
    names=[f'item-{i}.json' for i in range(20)]
    for name in names:(folder/name).write_text(json.dumps('x'*100))
    configure(folder,names)
    assert len(initial.read('demo',base)) == initial.MAX_READS
    monkeypatch.setattr(initial,'MAX_TOTAL_BYTES',250)
    assert len(initial.read('demo',base)) == 2


def test_declared_medium_json_keeps_exact_payload_within_combined_envelope(app):
    base,folder=app
    payload=json.dumps({'items':['x' * (96 * 1024)]})
    names=['catalog.json','history.json','overflow.json']
    configure(folder,names)
    for name in names:(folder/name).write_text(payload)
    reads=initial.read('demo',base)
    assert reads == {'/app-static/demo/'+name:payload for name in names[:2]}
    assert sum(len(value.encode()) for value in reads.values()) <= initial.MAX_TOTAL_BYTES


@pytest.mark.parametrize('options',[None, [], 'bad', {'files':'items.json'}, {'data':{},'files':[]}])
def test_unconfigured_or_malformed_manifest_keeps_original_html(app, options):
    base,folder=app
    (folder/'app.json').write_text(json.dumps({'startup_reads':options}))
    html='<head><script>app()</script></head>'
    assert initial.application_page(html,'demo',base) == html


def test_html_escapes_data_and_places_exact_adapter_before_app_code(app):
    base,folder=app
    configure(folder,['items.json'])
    (folder/'items.json').write_text(json.dumps({'text':'</script><script>unsafe()</script>&'}))
    html=initial.application_page('<head><script>app()</script></head>','demo',base)
    assert '\\u003c' in html and '<script>unsafe()' not in html
    assert html.index('cccAppInitialReads') < html.index('/static/app-read-boot.js') < html.index('app()')


def test_full_application_builder_uses_inline_adapter_and_current_payload(app,monkeypatch):
    import server
    base,folder=app
    configure(folder,['items.json'])
    payload = {'count': 1, 'markup': '<link href="/static/app.css">'}
    (folder/'items.json').write_text(json.dumps(payload))
    (folder/'index.html').write_text(
        '<head><link href="/static/app.css"><script>app()</script></head>')
    monkeypatch.setattr(server,'USER_APPS_DIR',base)
    html=server._load_application_html('demo')
    prefix = 'data-ccc-bootstrap-src="/static/app-read-boot.js?v='
    assert html.index('cccAppInitialReads') < html.index(prefix) < html.index('app()')
    assert 'root.CCCAppReads = Object.freeze' in html
    assert f'href="{server._static_asset_url("app.css")}"' in html
    from tests.test_app_page_reader import _document_payload
    embedded = _document_payload(html, 'cccAppInitialReads')['reads']
    assert json.loads(embedded['/app-static/demo/items.json']) == payload
    assert '<script data-ccc-inline-bootstrap src=' not in html


def test_data_read_byte_bound_preserves_normal_api_and_exact_limit(app):
    base,folder=app
    configure(folder)
    text=json.dumps({'value':'café','updated':1},ensure_ascii=False)
    (folder/'data'/'choice.json').write_text(text)
    canonical=app_data.read('demo','choice',base)
    assert canonical[1] == 200
    size=len(text.encode('utf-8'))
    assert app_data.read('demo','choice',base,max_bytes=size) == canonical
    assert app_data.read('demo','choice',base,max_bytes=size-1)[1] == 500
    assert app_data.read('demo','choice',base) == canonical
