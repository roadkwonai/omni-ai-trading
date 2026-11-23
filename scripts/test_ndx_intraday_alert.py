from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ndx_intraday_alert import closing, evaluate

NY = ZoneInfo('America/New_York')


def test_crossed_low_remains_detectable_after_recovery_and_incomplete_bar_ignored():
    start = datetime(2026, 11, 23, 9, 30, tzinfo=NY)
    end = start.replace(hour=16, minute=0)
    times = [int((start + timedelta(minutes=n)).timestamp()) for n in (0, 1, 2)]
    data = {'meta': {'symbol': '^NDX', 'currentTradingPeriod': {'regular': {'start': start.timestamp(), 'end': end.timestamp()}}},
            'timestamp': times, 'indicators': {'quote': [{'low': [79, 81, 70]}]}}
    result = evaluate(data, 100, -.2, (start + timedelta(minutes=2, seconds=30)).astimezone(timezone.utc))
    assert result['status'] == 'crossed' and result['crossed_low'] == 79 and result['low'] == 81


def test_delayed_missing_or_closed_never_alerts():
    start = datetime(2026, 11, 27, 9, 30, tzinfo=NY)
    end = start.replace(hour=13, minute=0)
    data = {'meta': {'symbol': '^NDX', 'currentTradingPeriod': {'regular': {'start': start.timestamp(), 'end': end.timestamp()}}},
            'timestamp': [int(start.timestamp())], 'indicators': {'quote': [{'low': [79]}]}}
    assert evaluate(data, 100, -.2, start + timedelta(minutes=10))['status'] == 'delayed'
    assert evaluate(data, 100, -.2, end + timedelta(minutes=31))['status'] == 'outside_session'
    assert evaluate(data, None, -.2, start)['status'] == 'unavailable'


def test_one_alert_per_session_persists_after_provider_failure(tmp_path, monkeypatch):
    import ndx_intraday_alert as monitor

    start = datetime(2026, 11, 23, 9, 30, tzinfo=NY)
    data = {'meta': {'symbol': '^NDX', 'currentTradingPeriod': {'regular': {
        'start': start.timestamp(), 'end': start.replace(hour=16, minute=0).timestamp()}}},
        'timestamp': [int(start.timestamp())], 'indicators': {'quote': [{'low': [79]}]}}
    monkeypatch.setattr(monitor.L, 'read_state', lambda *a: {
        'plan': {'indicators': {'ath': {'ndx': {'peak': 100, 'as_of': '2026-11-20'}}}}})
    monkeypatch.setattr(monitor.L, 'load_settings', lambda *a: {})
    monkeypatch.setattr(monitor.L, 'yeo_settings', lambda *a: {'market': 'us', 'us_index': 'ndx'})
    now = start + timedelta(minutes=2)
    first = monitor.run(now, tmp_path, lambda: data)
    second = monitor.run(now, tmp_path, lambda: data)
    assert first['alert'] is True and second['alert'] is False
    assert second['crossing']['crossed_low'] == 79


def test_closing_requires_session_and_settlement():
    start = datetime(2026, 11, 27, 9, 30, tzinfo=NY)
    end = start.replace(hour=13, minute=0)
    data = {'meta': {'symbol': '^NDX', 'regularMarketTime': end.timestamp()}, 'timestamp': [start.timestamp()],
            'indicators': {'quote': [{'close': [79]}]}}
    assert closing(data, '2026-11-27', end.timestamp(), 100, -.2, end)['status'] == 'unavailable'
    final = closing(data, '2026-11-27', end.timestamp(), 100, -.2, end + timedelta(minutes=15))
    assert final['status'] == 'confirmed' and final['qualifies'] is True
    assert closing(data, '2026-11-30', end.timestamp(), 100, -.2, end + timedelta(minutes=15))['status'] == 'unavailable'


def test_last_minute_crossing_can_be_observed_after_early_close():
    start = datetime(2026, 11, 27, 9, 30, tzinfo=NY)
    end = start.replace(hour=13, minute=0)
    ts = (end - timedelta(minutes=1)).timestamp()
    data = {'meta': {'symbol': '^NDX', 'currentTradingPeriod': {'regular': {'start': start.timestamp(), 'end': end.timestamp()}}},
            'timestamp': [ts], 'indicators': {'quote': [{'low': [80]}]}}
    result = evaluate(data, 100, -.2, end + timedelta(minutes=10))
    assert result['status'] == 'crossed' and result['crossed_low'] == 80
