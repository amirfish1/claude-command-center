"""Archive recency keeps finite seconds and legacy ticket timestamp semantics."""
import pytest
import server


@pytest.mark.parametrize('field', [
    'modified', 'mtime', 'last_interacted', 'last_activity', 'last_mtime',
    'archived_at', 'closed_at', 'started_at',
])
@pytest.mark.parametrize(('value', 'expected'), [
    (None, 0), ('', 0), (False, 0), (True, 1), (0, 0), (-10, 0),
    (12.5, 12.5), ('42.5', 42.5), (1700000000123, 1700000000.123),
    ('1700000000123', 1700000000.123), ('invalid', 0),
    (float('nan'), 0), (float('inf'), 0), (float('-inf'), 0),
    ([], 0), ({'unexpected': 'value'}, 0),
])
def test_archive_recency_timestamp_values(field, value, expected):
    assert server._archive_list_row_ts({field: value}) == expected


def test_archive_recency_uses_newest_field_and_ticket_move():
    row = {'mtime': '17', 'modified': 18000, 'started_at': 17000,
           'idea_ticket': {'moved_at': '19000'}}
    assert server._archive_list_row_ts(row) == 19000
    row['idea_ticket']['moved_at'] = float('nan')
    assert server._archive_list_row_ts(row) == 18000
    row['idea_ticket']['moved_at'] = 'not-a-number'
    assert server._archive_list_row_ts(row) == 18000
    # Ticket moved_at was already seconds; preserve its existing contract.
    row['idea_ticket']['moved_at'] = 1700000000123
    assert server._archive_list_row_ts(row) == 1700000000123
    assert server._archive_list_row_ts(None) == 0
