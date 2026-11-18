"""Ticket inheritance follows graph structure independently of activity writes."""
from ccc_server import idea_tickets as tickets
from ccc_server.session_graph import _SessionGraph


def test_activity_reuses_projection_but_local_and_external_lineage_refresh(tmp_path, monkeypatch):
    graph = _SessionGraph(tmp_path / 'graph.json')
    graph.add_historical_spawn('root', 'child', 1)
    graph.note_ticket_session('child')
    assert graph.save()
    monkeypatch.setattr(tickets, '_graph', lambda: graph)
    monkeypatch.setattr(tickets, '_state_cache', {'key': None, 'data': None})
    raw = {'schema': 2, 'sessions': {'root': {'ref': 'IDEAS-1'}},
           'tickets': {'IDEAS-1': {}}, 'pending': {}}
    first = tickets._project_state(raw)
    assert first['sessions']['child']['ref'] == 'IDEAS-1'
    graph.record_activity([{'id': 'wake', 'type': 'wake', 'receiver': 'child', 'at': 2}])
    assert tickets._project_state(raw) is first

    graph.add_continuation('child', 'continued', 3)
    graph.note_ticket_session('continued')
    second = tickets._project_state(raw)
    assert second is not first
    assert second['sessions']['continued']['ref'] == 'IDEAS-1'
    assert graph.save()

    writer = _SessionGraph(graph._path)
    writer.load()
    writer.add_historical_spawn('root', 'external', 4)
    writer.note_ticket_session('external')
    assert writer.save()
    third = tickets._project_state(raw)
    assert third is not second
    assert third['sessions']['external']['ref'] == 'IDEAS-1'
    writer.record_activity([{'id': 'external-wake', 'type': 'wake', 'receiver': 'external', 'at': 5}])
    assert tickets._project_state(raw) is third

    replacement = _SessionGraph(graph._path)
    replacement.load()
    monkeypatch.setattr(tickets, '_graph', lambda: replacement)
    assert tickets._project_state(raw) == third
    assert tickets._project_state(raw) is not third
