"""The mobile Apps menu consumes the same catalog as the desktop rail."""
def test_guided_decisions_is_a_navigable_application(tmp_path, monkeypatch):
    import server
    monkeypatch.setattr(server, 'COMMAND_CENTER_STATE_DIR', tmp_path)
    monkeypatch.setattr(server, 'USER_APPS_DIR', tmp_path / 'apps')
    apps = {app['id']: app for app in server._resolve_apps()}
    assert apps['decision-guide']['label'] == 'Decisions'
    assert apps['decision-guide']['nav'] == '/decision-guide.html'
    assert 'decision-inbox' not in apps
