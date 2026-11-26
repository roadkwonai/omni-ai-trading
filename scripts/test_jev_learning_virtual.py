import pytest

from jev_learning_virtual import replay


LIMITS = {'order_krw': 10000, 'daily_buy_krw': 10000, 'daily_new_symbols': 5, 'held_krw': 10000}


def steps():
    return [
        {'id': 'one', 'at': '2026-11-25T10:00:00+09:00', 'symbol': '005930',
         'source_at': '2026-11-25T10:00:00+09:00', 'received_at': '2026-11-25T10:00:00+09:00',
         'bid': 99, 'ask': 100, 'decisions': {'baseline': {'action': 'WAIT', 'decided_at': '2026-11-25T10:00:00+09:00'},
         'experience': {'action': 'BUY', 'qty': 2, 'limit': 100, 'decided_at': '2026-11-25T10:00:00+09:00'}}},
        {'id': 'two', 'at': '2026-11-25T10:01:00+09:00', 'symbol': '005930',
         'source_at': '2026-11-25T10:01:00+09:00', 'received_at': '2026-11-25T10:01:00+09:00',
         'bid': 110, 'ask': 111, 'decisions': {'baseline': {'action': 'WAIT', 'decided_at': '2026-11-25T10:01:00+09:00'},
         'experience': {'action': 'SELL', 'qty': 2, 'limit': 110, 'decided_at': '2026-11-25T10:01:00+09:00'}}},
    ]


def test_independent_books_compare_confirmed_virtual_fills():
    result = replay(steps(), cash=1000, limits=LIMITS, fee=0)
    assert result['baseline']['cash'] == 1000
    assert result['experience']['cash'] == 1020
    assert result['experience']['orders'] == 2
    assert result['experience']['fills'] == 2
    assert result['experience']['realized_krw'] == 20
    assert result['baseline']['orders'] == 0


def test_rejects_late_evidence_and_duplicate_ids():
    data = steps()
    data[0]['received_at'] = '2026-11-25T10:00:01+09:00'
    with pytest.raises(ValueError):
        replay(data, cash=1000, limits=LIMITS, fee=0)
    data = steps()
    data[1]['id'] = data[0]['id']
    with pytest.raises(ValueError):
        replay(data, cash=1000, limits=LIMITS, fee=0)


def test_resting_order_blocks_next_and_is_not_a_fill():
    data = steps()
    data[0]['decisions']['experience']['limit'] = 98
    result = replay(data, cash=1000, limits=LIMITS, fee=0)
    assert result['experience']['orders'] == 1
    assert result['experience']['fills'] == 0
    assert result['experience']['pending'] == 1
    assert result['experience']['events'][1]['status'] == 'blocked_pending'


def test_decision_from_future_is_rejected():
    data = steps()
    data[0]['decisions']['experience']['decided_at'] = '2026-11-25T10:00:01+09:00'
    with pytest.raises(ValueError):
        replay(data, cash=1000, limits=LIMITS, fee=0)


def test_end_valuation_uses_original_quote_time():
    data = steps()
    data[0]['source_at'] = '2026-11-25T09:59:35+09:00'
    data[1]['at'] = data[1]['source_at'] = data[1]['received_at'] = '2026-11-25T10:00:10+09:00'
    data[1]['symbol'] = '000660'
    for arm in data[1]['decisions']:
        data[1]['decisions'][arm] = {'action': 'WAIT', 'decided_at': data[1]['at']}
    result = replay(data, cash=1000, limits=LIMITS, fee=0)
    assert result['experience']['holdings']
    assert result['experience']['liquidation_value_krw'] is None


def test_equity_curve_includes_fees_and_sampled_drawdown():
    data = steps()
    data[1]['bid'], data[1]['ask'] = 80, 81
    data[1]['decisions']['experience']['limit'] = 80
    result = replay(data, cash=1000, limits=LIMITS, fee=0.01)
    book = result['experience']
    assert book['equity_curve'][0]['before_krw'] == 1000
    assert book['equity_curve'][0]['after_krw'] == pytest.approx(994.02)
    assert book['equity_curve'][1]['before_krw'] == pytest.approx(956.4)
    assert book['equity_curve'][1]['after_krw'] == pytest.approx(956.4)
    assert book['sampled_max_drawdown_krw'] == pytest.approx(43.6)
    assert book['sampled_max_drawdown_pct'] == pytest.approx(4.36)
    assert book['missing_equity_marks'] == 0


def test_missing_mark_is_excluded_and_counted():
    data = steps()
    data[0]['source_at'] = '2026-11-25T09:59:35+09:00'
    data[1]['at'] = data[1]['source_at'] = data[1]['received_at'] = '2026-11-25T10:00:10+09:00'
    data[1]['symbol'] = '000660'
    for arm in data[1]['decisions']:
        data[1]['decisions'][arm] = {'action': 'WAIT', 'decided_at': data[1]['at']}
    book = replay(data, cash=1000, limits=LIMITS, fee=0)['experience']
    assert book['equity_curve'][1]['before_krw'] is None
    assert book['equity_curve'][1]['after_krw'] is None
    assert book['missing_equity_marks'] == 2
    assert book['sampled_max_drawdown_krw'] == 2
