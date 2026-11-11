from tests.test_oncall_http import port, _call


def test_oncall_status_refuses_foreign_browser_origin(port, monkeypatch):
    monkeypatch.setenv('CCC_FF_ONCALL', '1')
    assert _call(port, 'GET', '/api/oncall/status', headers={'Origin': 'https://attacker.invalid'})[0] == 403
