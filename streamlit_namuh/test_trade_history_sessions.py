from unittest.mock import Mock

import trade_history_sessions as sessions


def test_disconnected_lease_released_after_grace_and_reconnect_cancels_timeout():
    lease = Mock()
    sessions._watched[lease] = ('session', None)
    sessions._reap(lambda _: False, grace=120, now=10)
    sessions._reap(lambda _: False, grace=120, now=129)
    lease.release.assert_not_called()
    sessions._reap(lambda _: True, grace=120, now=130)
    sessions._reap(lambda _: False, grace=120, now=200)
    sessions._reap(lambda _: False, grace=120, now=320)
    lease.release.assert_called_once()
    sessions._reap(lambda _: False, grace=120, now=500)
    lease.release.assert_called_once()


def test_session_status_failure_keeps_lease(caplog):
    lease = Mock()
    sessions._watched[lease] = ('session', None)

    def unavailable(_):
        raise RuntimeError('runtime unavailable')

    sessions._reap(unavailable, grace=0, now=100)
    sessions._reap(unavailable, grace=0, now=101)
    assert sum('세션 상태를 확인하지 못해' in record.message for record in caplog.records) == 1
    lease.release.assert_not_called()
    sessions._reap(lambda _: False, grace=0, now=200)
    sessions._reap(lambda _: False, grace=0, now=201)
    lease.release.assert_called_once()


def test_closed_tab_cleanup_preserves_another_tab_sharing_its_snapshot(tmp_path, monkeypatch):
    from datetime import date
    import pytest
    import trade_history_data as data

    monkeypatch.setattr(data, 'load', lambda _: ())
    monkeypatch.setattr(data, 'revision', lambda _: (1,))
    filters = (('paper-shared',), date(2026, 11, 26), date(2026, 11, 26), None, None, None)
    first, _ = data.acquire_query(tmp_path, filters)
    second, _ = data.acquire_query(tmp_path, filters)
    assert first.token == second.token
    sessions._watched[first] = ('closed', None)
    sessions._watched[second] = ('open', None)
    def active(session):
        return session == 'open'
    sessions._reap(active, grace=120, now=0)
    sessions._reap(active, grace=120, now=120)
    assert len(data.get_query(second.token)) == 0
    sessions.release(second)
    with pytest.raises(data.QueryExpired):
        data.get_query(first.token)
    first.release()
