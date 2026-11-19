from decimal import Decimal

import engine_exits as e

SETTINGS = {'stop_loss_pct': 2.0, 'take_profit_half_pct': 1.0, 'take_profit_all_pct': 2.0}


def test_hits_are_exact_at_the_threshold():
    lv = e.levels(e.defensive_plan(SETTINGS), None, None, '069500')
    assert lv['stop'] == Decimal('0.98') and lv['all'] == Decimal('1.02')
    assert e.target_hit(1345, 1371.9, lv, 'all') and not e.target_hit(1345, 1371.89, lv, 'all')
    assert e.stop_hit(1000, 980, lv) and not e.stop_hit(1000, 981, lv)
    assert not e.target_hit(1000, 10**6, {**lv, 'all': None}, 'all')  # a trend plan has no fixed final target

