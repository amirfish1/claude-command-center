"""Repository logo detection, image confinement and persisted overrides."""
import base64
from pathlib import Path

import pytest

from ccc_server import repo_avatars as avatars


def test_logo_precedes_favicon_and_is_cached(tmp_path):
    repo = tmp_path / 'sample-project'
    (repo / 'public').mkdir(parents=True)
    (repo / 'public/favicon.png').write_bytes(b'favicon')
    (repo / 'public/logo.svg').write_text('<svg/>')
    image, kind, mode, source = avatars.avatar(repo, tmp_path / 'state')
    assert (image, kind, mode, source) == (b'<svg/>', 'image/svg+xml', 'automatic', 'public/logo.svg')


def test_readme_local_logo_and_external_symlink_confinement(tmp_path):
    repo = tmp_path / 'project'
    repo.mkdir()
    outside = tmp_path / 'private.svg'
    outside.write_text('private image')
    (repo / 'logo.svg').symlink_to(outside)
    (repo / 'README.md').write_text('![badge](https://example.com/badge.svg)\n![Logo](branding/project.svg)')
    (repo / 'branding').mkdir()
    (repo / 'branding/project.svg').write_text('<svg>brand</svg>')
    assert avatars.avatar(repo, tmp_path / 'state')[3] == 'branding/project.svg'
    assert avatars._image(repo / '../private.svg', repo) is None


def test_initials_are_stable_and_escaped(tmp_path):
    repo = tmp_path / 'sample-project'
    repo.mkdir()
    first = avatars.avatar(repo, tmp_path / 'state')
    assert first == avatars.avatar(repo, tmp_path / 'state')
    assert first[2] == 'initials'
    assert b'>SP</text>' in first[0]


def test_custom_avatar_persists_per_repo_and_reset_detects_again(tmp_path):
    repo = tmp_path / 'sample-project'
    repo.mkdir()
    state = tmp_path / 'state'
    url = 'data:image/png;base64,' + base64.b64encode(b'custom image').decode()
    avatars.save(repo, state, url)
    assert avatars.avatar(repo, state)[:3] == (b'custom image', 'image/png', 'custom')
    assert avatars.avatar(tmp_path / 'other', state)[2] == 'initials'
    (repo / 'icon.svg').write_text('<svg/>')
    avatars.save(repo, state, None)
    assert avatars.avatar(repo, state)[2:] == ('automatic', 'icon.svg')


@pytest.mark.parametrize('value', ['https://example.com/logo.png', 'data:text/html;base64,YQ==',
                                    'data:image/png;base64,!', 12, 'data:image/png;base64,'])
def test_reject_invalid_uploads(tmp_path, value):
    with pytest.raises(ValueError):
        avatars.save(tmp_path, tmp_path / 'state', value)


def test_skip_oversized_assets(tmp_path):
    (tmp_path / 'logo.png').write_bytes(b'x' * (avatars.MAX_IMAGE + 1))
    assert avatars.avatar(tmp_path, tmp_path / 'state')[2] == 'initials'


@pytest.fixture
def avatar_api(tmp_path, monkeypatch):
    import http.server
    import threading
    import server

    repo = tmp_path / 'sample-project'
    (repo / '.git').mkdir(parents=True)
    monkeypatch.setattr(server, 'COMMAND_CENTER_STATE_DIR', tmp_path / 'state')
    monkeypatch.setattr(server, '_known_repo_paths', lambda: [str(repo)])
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.CommandCenterHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    yield 'http://127.0.0.1:' + str(httpd.server_port), repo
    httpd.shutdown()
    httpd.server_close()
    worker.join(timeout=5)


def test_avatar_http_upload_reload_reset_and_headers(avatar_api):
    import json
    import urllib.parse
    import urllib.request

    base, repo = avatar_api
    path = '/api/repo/avatar?repo_path=' + urllib.parse.quote(str(repo))
    with urllib.request.urlopen(base + path) as response:
        assert response.headers['Content-Type'] == 'image/svg+xml'
        assert 'sandbox' in response.headers['Content-Security-Policy']
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        assert b'>SP</text>' in response.read()
    image = 'data:image/svg+xml;base64,' + base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"/>').decode()
    for upload, expected in ((image, 'custom'), (None, 'initials')):
        request = urllib.request.Request(base + '/api/repo/avatar',
            data=json.dumps({'repo_path': str(repo), 'image': upload}).encode(),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request) as response:
            assert json.load(response)['ok']
        with urllib.request.urlopen(base + '/api/repo/avatar/info?repo_path=' + urllib.parse.quote(str(repo))) as response:
            assert json.load(response)['mode'] == expected


def test_avatar_http_rejects_unknown_repo_and_foreign_origin(avatar_api):
    import json
    import urllib.error
    import urllib.request

    base, repo = avatar_api
    with pytest.raises(urllib.error.HTTPError) as rejected:
        urllib.request.urlopen(base + '/api/repo/avatar?repo_path=%2F')
    assert rejected.value.code == 400
    request = urllib.request.Request(base + '/api/repo/avatar',
        data=json.dumps({'repo_path': str(repo), 'image': None}).encode(),
        headers={'Content-Type': 'application/json', 'Origin': 'https://example.com'})
    with pytest.raises(urllib.error.HTTPError) as rejected:
        urllib.request.urlopen(request)
    assert rejected.value.code == 403
