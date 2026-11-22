"""Overlapping sidebar reads share work without retaining stale snapshots."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

import server


@pytest.fixture
def flights(monkeypatch):
    monkeypatch.setattr(server, '_ARCHIVE_LIST_SOURCE_FLIGHTS', {})
    monkeypatch.setattr(server, '_archive_response_cache_key',
                        lambda **options: tuple(sorted(options.items())))
    return server._ARCHIVE_LIST_SOURCE_FLIGHTS


class ObservedEvent:
    """Signal when a follower has actually joined, without timing sleeps."""
    def __init__(self):
        self.event = threading.Event()
        self.waiting = threading.Event()

    def wait(self):
        self.waiting.set()
        assert self.event.wait(5)

    def set(self):
        self.event.set()


@pytest.mark.parametrize('fail_owner', [False, True])
def test_overlap_cleanup_and_next_poll_freshness(monkeypatch, flights, fail_owner):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def read(options, **kwargs):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
            kwargs['check_client']()
            if fail_owner:
                raise ValueError('source unavailable')
        return [{'session_id': str(len(calls))}], True, len(calls)

    monkeypatch.setattr(server, '_archive_list_source_rows_read', read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        def disconnected():
            raise server._ArchiveListClientDisconnected()

        owner = pool.submit(server._archive_list_source_rows_cached, {},
                            check_client=disconnected)
        assert entered.wait(5)
        observed = ObservedEvent()
        # Replace only the blocked owner's event, before the follower joins.
        next(iter(flights.values()))['event'] = observed
        phases = {}
        follower = pool.submit(server._archive_list_source_rows_cached, {}, phases=phases)
        assert observed.waiting.wait(5)
        release.set()
        if fail_owner:
            with pytest.raises(ValueError, match='source unavailable'):
                owner.result(timeout=5)
            assert follower.result(timeout=5)[2] == 2
            assert 'coalesced' not in phases
        else:
            assert follower.result(timeout=5) is owner.result(timeout=5)
            assert calls == [1]
            assert phases['coalesced'] is True
    assert flights == {}
    before = len(calls)
    assert server._archive_list_source_rows_cached({})[2] == before + 1
    assert flights == {}


def test_freshness_and_scope_do_not_wait_for_another_read(monkeypatch, flights):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def read(options, *, force_refresh, window, **kwargs):
        calls.append((options, force_refresh, window))
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return [], True, len(calls)

    monkeypatch.setattr(server, '_archive_list_source_rows_read', read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(server._archive_list_source_rows_cached, {}, window='7d')
        assert entered.wait(5)
        try:
            for options, fresh, window in [({}, True, '7d'), ({}, False, '1d'),
                                           ({'include_prs': True}, False, '7d')]:
                other = pool.submit(server._archive_list_source_rows_cached, options,
                                    force_refresh=fresh, window=window)
                assert other.result(timeout=5)[2] == len(calls)
            assert len(calls) == 4
            assert len(flights) == 1
        finally:
            release.set()
            owner.result(timeout=5)
    assert flights == {}


def test_disconnected_follower_releases_abandoned_source_owner(monkeypatch, flights):
    entered, release, waiting, follower_disconnected = (
        threading.Event() for _ in range(4)
    )
    calls = []

    def read(_options, check_client, **_kwargs):
        calls.append('read')
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        check_client()
        return [], True, 1

    def disconnected_owner():
        raise server._ArchiveListClientDisconnected()

    def check_follower():
        if follower_disconnected.is_set():
            raise server._ArchiveListClientDisconnected()

    monkeypatch.setattr(server, '_archive_list_source_rows_read', read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(server._archive_list_source_rows_cached, {},
                            check_client=disconnected_owner)
        try:
            assert entered.wait(5)
            event = next(iter(flights.values()))['event']
            original_wait = event.wait

            def observe_wait(timeout=None):
                waiting.set()
                return original_wait(timeout=timeout)

            monkeypatch.setattr(event, 'wait', observe_wait)
            follower = pool.submit(server._archive_list_source_rows_cached, {},
                                   check_client=check_follower)
            assert waiting.wait(5)
            follower_disconnected.set()
            with pytest.raises(server._ArchiveListClientDisconnected):
                follower.result(timeout=2)
        finally:
            release.set()
        with pytest.raises(server._ArchiveListClientDisconnected):
            owner.result(timeout=5)

    assert calls == ['read']
    assert flights == {}
    assert server._archive_list_source_rows_cached({}, check_client=lambda: None) == ([], True, 1)
