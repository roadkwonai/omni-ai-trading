from datetime import timedelta

from test_vibe_fast_exit import NOW, inputs
from vibe_event_trigger import detect


def sample():
    data = inputs()
    return {key: value for key, value in data.items() if key not in ('advice', 'context_status', 'existing_exit')} | {
        'position_id': 'a' * 20, 'qty': 10, 'quote_observed_at': NOW - timedelta(seconds=1),
    }


def test_rise_retracement_is_a_price_event_without_model():
    result = detect(**sample())
    assert result['state'] == 'triggered'
    assert result['reasons'] == ['rapid_rise_retracement']
    assert result['inputs']['window_peak'] == 102
    assert result['mode'] == 'observation_only'


def test_falling_position_can_trigger_before_existing_stop():
    data = sample()
    data['bars'] = data['bars'][-3:]
    for row, close in zip(data['bars'], (100, 99.7, 99.4)):
        row.update(stck_oprc=100, stck_hgpr=100, stck_lwpr=99, stck_prpr=close, vol=100)
    result = detect(**data)
    assert result['state'] == 'triggered' and result['reasons'] == ['rapid_loss']


def test_valid_quotes_with_no_retracement_or_falling_pattern_wait():
    data = sample()
    data['quote']['bidp'] = 102
    result = detect(**data)
    assert result['state'] == 'wait' and not result['triggered']


def test_untrusted_or_stale_inputs_never_trigger():
    for mutation, reason in (
        (lambda d: d.update(profile='oversold_v2'), 'non_general_position'),
        (lambda d: d.update(position_id='invalid'), 'position_identity_invalid'),
        (lambda d: d.update(qty=True), 'position_identity_invalid'),
        (lambda d: d.update(entry_at=NOW + timedelta(seconds=1)), 'position_invalid'),
        (lambda d: d.update(bars_symbol='005930'), 'bars_symbol_mismatch'),
        (lambda d: d.update(bars_observed_at=NOW - timedelta(seconds=31)), 'bars_observation_stale'),
        (lambda d: d['quote'].update(iem_cd='005930'), 'quote_invalid_or_stale'),
        (lambda d: d['quote'].update(hoga_bsop_hour='12:39:59'), 'quote_invalid_or_stale'),
        (lambda d: d.update(quote_observed_at=NOW + timedelta(seconds=1)), 'quote_invalid_or_stale'),
        (lambda d: d.update(quote_observed_at=NOW - timedelta(seconds=10)), 'quote_invalid_or_stale'),
    ):
        data = sample()
        mutation(data)
        result = detect(**data)
        assert result['state'] == 'unavailable' and not result['triggered']
        assert reason in result['unavailable']


def test_incomplete_duplicate_gapped_and_pre_entry_bars_do_not_trigger():
    for mutation in (
        lambda d: d['bars'].pop(2),
        lambda d: d['bars'].__setitem__(2, d['bars'][1]),
        lambda d: d['bars'][0].update(bsop_date='20261113'),
        lambda d: d['bars'][0].update(stck_hgpr=90),
        lambda d: d.update(entry_at=NOW - timedelta(minutes=3)),
        lambda d: d.update(bars_observed_at=NOW - timedelta(minutes=2)),
    ):
        data = sample()
        mutation(data)
        assert not detect(**data)['triggered']


def test_pre_entry_and_incomplete_highs_are_not_used():
    data = sample()
    data['bars'].insert(0, {**data['bars'][0], 'bsop_time': '123200', 'stck_hgpr': 1000})
    data['bars'].extend([{**data['bars'][-1], 'bsop_time': '123900', 'stck_hgpr': 1000},
                         {**data['bars'][-1], 'bsop_time': '124000', 'stck_hgpr': 1000}])
    data['entry_at'] = NOW - timedelta(minutes=7, seconds=30)
    result = detect(**data)
    assert result['triggered'] and result['inputs']['window_peak'] == 102
