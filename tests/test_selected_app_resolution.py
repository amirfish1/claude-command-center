"""A deep link resolves one app without rereading unrelated manifests."""
import json
from pathlib import Path

import server


def test_selected_resolution_matches_catalog_without_scanning_neighbors(tmp_path, monkeypatch):
    apps = tmp_path / 'apps'
    for name in ('demo', 'other'):
        folder = apps / name
        folder.mkdir(parents=True)
        (folder / 'app.json').write_text(json.dumps({'label':name}))
        (folder / 'index.html').write_text('<main>Example</main>')
    monkeypatch.setattr(server, 'COMMAND_CENTER_STATE_DIR', tmp_path)
    monkeypatch.setattr(server, 'USER_APPS_DIR', apps)
    expected = [a for a in server._resolve_apps() if a['id'] == 'demo']
    read = Path.read_text
    def checked(candidate, *args, **kwargs):
        assert 'other' not in candidate.parts, 'read an unrelated app'
        return read(candidate, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', checked)
    assert server._resolve_apps(only_id='demo') == expected
    assert server._resolve_apps(only_id='missing') == []
    for unsafe in ('../demo', '/demo', 'demo/other', 'x' * 41):
        assert server._resolve_apps(only_id=unsafe) == []
    (apps / 'demo' / 'app.json').write_text(json.dumps({'label':'Renamed'}))
    assert server._resolve_apps(only_id='demo')[0]['label'] == 'Renamed'


def test_selected_manifest_override_and_disabled_rules_match_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'COMMAND_CENTER_STATE_DIR', tmp_path)
    monkeypatch.setattr(server, 'USER_APPS_DIR', tmp_path / 'apps')
    config = tmp_path / 'custom-links.json'
    config.write_text(json.dumps({'apps':[{'id':'demo','label':'Override','url':'/custom'}],
                                 'app_disabled':['demo','sessions']}))
    for disabled in (False, True):
        all_apps = server._resolve_apps(include_disabled=disabled)
        for name in ('demo','sessions','queues'):
            assert server._resolve_apps(include_disabled=disabled, only_id=name) == [a for a in all_apps if a['id'] == name]
    assert server._resolve_apps(only_id='demo') == []
    config.write_text(json.dumps({'apps':[{'id':'demo','label':'Edited','url':'/new'}]}))
    assert server._resolve_apps(only_id='demo')[0]['nav'] == '/new'
