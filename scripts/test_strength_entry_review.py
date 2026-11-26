from datetime import datetime, timedelta

import strength_entry_review as policy


def inputs():
    at = datetime.fromisoformat('2026-11-26T10:06:05+09:00')
    own = {'as_of': '10:04', 'from_open_pct': .5, 'change_30m_pct': .4}
    return {'symbol': '100790', 'model_choice': '100790', 'model_fresh': True, 'now': at,
            'market': {'as_of': '2026-11-26T10:04', 'index_intraday': {'코스닥': own, '코스피': {**own, 'from_open_pct': -1}}},
            'index': '코스닥', 'market_news': [{'published': '2026-11-26T10:03:00+09:00'}],
            'stock': {'headlines': [{'age_min': 8}], 'investor_read_at': (at - timedelta(minutes=5)).isoformat(),
                      'investor_daily_net_qty': {'today_provisional': {'date': '20261126', 'foreign': 20}}},
            'timing': {'open_gap_pct': 4, 'ask_vs_recent_low_pct': .5, 'ask_vs_decision_pct': .2}}


def test_watchlist_rank_cannot_override_fresh_wait_or_approve_another_stock():
    facts = inputs()
    assert policy.review(**facts) == []
    assert policy.review(**{**facts, 'model_choice': 'WAIT'}) == ['strength_jev_wait']
    assert policy.review(**{**facts, 'model_choice': '005930'}) == ['strength_jev_other_symbol']
    assert policy.review(**{**facts, 'model_fresh': False}) == ['strength_model_unavailable']


def test_uses_own_market_and_refuses_unknown_index():
    facts = inputs()
    assert policy.review(**{**facts, 'index': '코스피'}) == ['strength_market_weak']
    assert policy.review(**{**facts, 'index': None}) == ['strength_market_unavailable']


def test_news_flow_and_quote_have_separate_block_reasons():
    facts = inputs()
    facts['market_news'] = [{'published': '2026-11-26T11:00:00+09:00'}]
    facts['stock']['headlines'] = [{'age_min': 40}]
    facts['stock']['investor_read_at'] = '2026-11-26T09:40:00+09:00'
    facts['timing']['open_gap_pct'] = 21.66
    assert policy.review(**facts) == ['strength_market_news_unverified', 'strength_stock_news_unverified',
                                      'strength_stock_flow_unverified', 'strength_chase']


def test_conditional_comparison_uses_three_valued_facts():
    facts = inputs()
    facts['model_choice'] = 'WAIT'
    facts['timing']['open_gap_pct'] = 9
    assert policy.compare(baseline=True, **facts)['conditional']['status'] == 'unknown'
    facts['market']['index_intraday']['코스닥']['change_30m_pct'] = -.5
    results = policy.compare(baseline=True, **facts)
    assert results['current']['status'] == 'permit'
    assert results['strict']['status'] == 'block'
    assert results['conditional'] == {'status': 'block', 'reasons': ['strength_jev_wait', 'strength_chase', 'strength_market_weak']}
    facts['model_choice'] = '100790'
    assert policy.compare(baseline=True, **facts)['conditional']['status'] == 'permit'
    assert policy.compare(baseline=False, **facts)['conditional']['status'] == 'block'


def test_conditional_pressure_requires_matching_fresh_source_and_receive_times():
    facts = inputs()
    facts['model_choice'] = 'WAIT'
    facts['timing']['open_gap_pct'] = 9
    at = facts['now']
    pressure = {'symbol': facts['symbol'], 'source': 'fixture', 'source_at': (at - timedelta(seconds=10)).isoformat(),
                'received_at': at.isoformat(), 'selling': True}
    assert policy.compare(baseline=True, pressure=pressure, **facts)['conditional']['status'] == 'block'
    for update in ({'symbol': '005930'}, {'source_at': (at + timedelta(seconds=1)).isoformat()},
                   {'received_at': (at - timedelta(minutes=6)).isoformat()}, {'selling': 1}, {'source': ''},
                   {'received_at': (at - timedelta(seconds=11)).isoformat()}):
        assert policy.compare(baseline=True, pressure={**pressure, **update}, **facts)['conditional']['status'] == 'unknown'


def test_known_chase_with_other_missing_timing_is_still_chase():
    facts = inputs()
    facts['model_choice'] = 'WAIT'
    facts['timing'] = {'open_gap_pct': 9}
    facts['market']['index_intraday']['코스닥']['change_30m_pct'] = -.5
    assert policy.compare(baseline=True, **facts)['conditional']['status'] == 'block'
