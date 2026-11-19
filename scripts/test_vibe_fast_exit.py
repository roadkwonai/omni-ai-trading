from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from vibe_fast_exit import evaluate


KST = ZoneInfo('Asia/Seoul')
NOW = datetime(2026, 11, 16, 12, 40, 30, tzinfo=KST)


def inputs():
    bars = []
    for minute in range(33, 39):
        bars.append({'bsop_date': '20261116', 'bsop_time': f'12{minute:02d}00',
                     'stck_oprc': 100, 'stck_hgpr': 102, 'stck_lwpr': 98,
                     'stck_prpr': 99, 'vol': 10 if minute < 36 else 30})
    return {'symbol': '100790', 'now': NOW, 'entry_at': NOW - timedelta(minutes=10),
            'entry_price': 100, 'profile': 'normal', 'existing_exit': False,
            'bars': bars, 'bars_symbol': '100790', 'bars_observed_at': NOW - timedelta(seconds=2),
            'quote': {'iem_cd': '100790', 'market_status': '1', 'hoga_bsop_hour': '12:40:25', 'bidp': 99},
            'advice': {'time': (NOW - timedelta(minutes=2)).isoformat(), 'grounded': True,
                       'confirmed_by_previous_run': True, 'signals': {'100790': -0.1},
                       'technical_basic': {'100790': {'as_of': '12:37'}}},
            'context_status': {'market': {'status': 'ok', 'observed_at': (NOW - timedelta(minutes=3)).isoformat()},
                               'news': {'status': 'ok', 'observed_at': (NOW - timedelta(minutes=4)).isoformat()}}}


def test_candidate_is_an_observation_with_traceable_inputs():
    result = evaluate(**inputs())
    assert result['state'] == 'candidate' and result['candidate']
    assert result['mode'] == 'observation_only' and result['unavailable'] == []
    assert result['inputs']['bars_from'].endswith('12:33:00+09:00')
    assert result['context_status'] == {'market': 'ok', 'news': 'ok'}
    assert '실제 체결 아님' in result['limitations']


def test_candidate_waits_without_negative_or_zero_model_signal():
    data = inputs()
    data['advice']['signals']['100790'] = 0.1
    result = evaluate(**data)
    assert result['state'] == 'wait' and result['not_met'] == ['model_not_positive']


def test_stale_untrusted_or_future_inputs_are_unavailable():
    for mutation, expected in (
        (lambda d: d.update(bars_observed_at=NOW - timedelta(seconds=31)), 'bars_observation_stale'),
        (lambda d: d.update(bars_symbol='005930'), 'bars_symbol_mismatch'),
        (lambda d: d['quote'].update(hoga_bsop_hour='12:40:31'), 'quote_invalid_or_stale'),
        (lambda d: d['quote'].update(iem_cd='005930'), 'quote_invalid_or_stale'),
        (lambda d: d['advice'].update(time=(NOW - timedelta(minutes=6)).isoformat()), 'model_unavailable_or_stale'),
        (lambda d: d['advice'].update(grounded=False), 'model_unavailable_or_stale'),
        (lambda d: d['advice']['technical_basic']['100790'].update(as_of='12:20'), 'model_technical_unavailable_or_stale'),
        (lambda d: d['advice']['technical_basic']['100790'].update(as_of='12:40'), 'model_technical_unavailable_or_stale'),
        (lambda d: d['context_status']['market'].update(status='partial'), 'market_unavailable_or_stale'),
        (lambda d: d['context_status']['market'].update(observed_at=(NOW + timedelta(seconds=1)).isoformat()), 'market_unavailable_or_stale'),
        (lambda d: d['context_status']['market'].update(observed_at=(NOW - timedelta(minutes=1)).isoformat()), 'market_after_model_start'),
        (lambda d: d['context_status']['news'].update(observed_at=(NOW - timedelta(minutes=31)).isoformat()), 'news_unavailable_or_stale'),
        (lambda d: d['advice']['technical_basic']['100790'].update(status='unavailable'), 'model_technical_unavailable_or_stale'),
        (lambda d: d.update(profile='oversold_v2'), 'non_general_position'),
        (lambda d: d.update(existing_exit=True), 'existing_exit_or_unknown'),
    ):
        data = inputs()
        mutation(data)
        result = evaluate(**data)
        assert result['state'] == 'unavailable' and expected in result['unavailable']


def test_missing_duplicate_incomplete_and_pre_entry_bars_never_count():
    for mutation in (
        lambda d: d['bars'].pop(2),
        lambda d: d['bars'].__setitem__(2, d['bars'][1]),
        lambda d: d['bars'][-1].update(bsop_time='123900'),
        lambda d: d['bars'][0].update(bsop_date='20261113'),
        lambda d: d['bars'][0].update(stck_hgpr=90),
        lambda d: d.update(entry_at=NOW - timedelta(minutes=5)),
    ):
        data = inputs()
        mutation(data)
        result = evaluate(**data)
        assert result['state'] == 'unavailable' and 'bars_invalid_or_insufficient' in result['unavailable']


def test_pre_entry_peak_cannot_create_a_signal():
    data = inputs()
    data['bars'].insert(0, {**data['bars'][0], 'bsop_time': '123200', 'stck_hgpr': 1000})
    data['entry_at'] = NOW - timedelta(minutes=7, seconds=30)
    result = evaluate(**data)
    assert result['candidate'] and result['inputs']['window_peak'] == 102


def test_current_and_previous_incomplete_minutes_are_ignored():
    data = inputs()
    data['bars'].extend([{**data['bars'][-1], 'bsop_time': '123900', 'stck_hgpr': 1000},
                         {**data['bars'][-1], 'bsop_time': '124000', 'stck_hgpr': 1000}])
    result = evaluate(**data)
    assert result['candidate'] and result['inputs']['window_peak'] == 102


def test_valid_bid_remains_observable_when_other_inputs_unavailable():
    data = inputs()
    data['advice']['grounded'] = False
    result = evaluate(**data)
    assert result['state'] == 'unavailable' and not result['candidate']
    assert result['inputs']['bid'] == 99
    assert 'model_unavailable_or_stale' in result['unavailable']
    data['quote']['hoga_bsop_hour'] = '12:39:00'
    result = evaluate(**data)
    assert 'bid' not in result['inputs'] and 'quote_invalid_or_stale' in result['unavailable']
