"""적립식 v3 V1 research rule (docs/longterm-dca-v3-plan.md 5): once a week, the master's day and size, capped."""
from datetime import date

import longterm_dca_v3_replay as V


def cfg(key, rate=0.01):
    label, mult, when, wait, floor, crash = V.MASTERS[key]
    return {'mult': mult, 'when': when, 'wait': wait, 'crash': crash, 'rate': rate}


def test_a_cycle_master_buys_on_the_weeks_first_day_once_a_week():
    state = {}
    assert V.v3_spend(cfg('bogle'), state, date(2026, 11, 16), 0.01, 15, 'bull', 1e8, 1e9) == 1e6  # 1% of 1억 x 1.0
    assert V.v3_spend(cfg('bogle'), state, date(2026, 11, 17), -0.01, 15, 'bull', 1e8, 1e9) == 0  # once a week


def test_a_dip_master_waits_for_a_down_day_or_the_max_wait_across_weeks_and_scales_by_regime():
    state = {}
    assert V.v3_spend(cfg('marks'), state, date(2026, 11, 16), 0.01, 15, 'bear', 1e8, 1e9) == 0  # up day: waits
    assert V.v3_spend(cfg('marks'), state, date(2026, 11, 17), -0.004, 15, 'bear', 1e8, 1e9) == 1.3e6  # down day, bear x1.3
    state = {}
    V.v3_spend(cfg('marks'), state, date(2026, 11, 19), 0.01, 15, 'bull', 1e8, 1e9)
    assert V.v3_spend(cfg('marks'), state, date(2026, 11, 24), 0.01, 15, 'bull', 1e8, 1e9) == 9e5  # 5 days waited (next week)


def test_the_size_is_capped_and_never_beyond_what_is_available():
    hot = {}
    assert V.v3_spend(cfg('yeo'), hot, date(2026, 11, 16), -0.01, 15, 'overheat', 1e8, 1e9) == 5e5  # overheat x0.5, never 0
    assert hot['catch'] == 5e5  # the half it did not buy is caught up later
    assert V.v3_spend(cfg('williams'), {}, date(2026, 11, 16), -0.04, 30, 'panic', 1e8, 1e9) == 1.5e6  # 1.2 x 1.5 capped at 1.5 x base
    assert V.v3_spend(cfg('bogle'), {}, date(2026, 11, 16), 0.01, 15, 'bull', 1e8, 3e5) == 3e5  # only what the pool or floor allows
