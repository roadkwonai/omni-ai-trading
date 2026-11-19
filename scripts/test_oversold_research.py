from datetime import datetime, timedelta

import oversold_research as o


def bars(closes, start=datetime(2026, 1, 5, 9, 0)):
    return [(start + timedelta(minutes=5 * i), c, c * 1.001, c * 0.999, c) for i, c in enumerate(closes)]


def test_stabilise_then_gap_recovery_target():
    # prev close 100, falls to ~90, stays flat (2 calm bars), then rebounds to the gap-recovery price
    b = bars([95, 90, 90.5, 90.6, 91, 100, 100])
    p = {'x': 3, 'k': 0, 'n': 2, 'y': 0, 'tp': 20, 'r': 1.0, 's': 5, 'hold': False}
    ret, why, adverse = o.simulate(b, 100, 5.0, None, p)
    assert why == 'target' and ret > 5 and adverse <= 0


def test_no_trigger_means_no_trade():
    p = {'x': 3, 'k': 0, 'n': 2, 'y': 0, 'tp': 3, 'r': 1.0, 's': 1, 'hold': False}
    assert o.simulate(bars([100, 100.5, 99.8, 100.2]), 100, 2.0, None, p) is None
