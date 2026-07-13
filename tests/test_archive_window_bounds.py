"""Archive windows must bound server work before rows are built."""

import server


def test_archive_window_maps_one_and_seven_days_to_server_cutoffs():
    now = 2_000_000

    assert server.archive_window_since_epoch("1d", now=now) == now - 86_400
    assert server.archive_window_since_epoch("7d", now=now) == now - 604_800
    assert server.archive_window_since_epoch("all", now=now) is None


def test_archive_window_rejects_unknown_window():
    assert server.archive_window_since_epoch("yesterday", now=2_000_000) is None
