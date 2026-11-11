from ccc_server import vault
from ccc_server.oncall import secret_filter


def test_outgoing_scan_uses_empty_fixture_vault(tmp_path, monkeypatch):
    assert vault._vault_state_dir() == tmp_path / 'vault'
    monkeypatch.setattr(vault, '_vault_secret_get', lambda name: (_ for _ in ()).throw(
        AssertionError('An empty fixture Vault must not read a secret backend')))
    assert secret_filter.mask('safe fixture text') == ('safe fixture text', False)
