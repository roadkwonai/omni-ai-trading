from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from oversold_reentry_replay import replay_after_stop
from strategy_settings import DEFAULTS

KST = ZoneInfo('Asia/Seoul')
DAYS = ['2026-11-09', '2026-11-10', '2026-11-11']
SETTINGS = {**DEFAULTS, 'flatten_hhmm': 1500, 'oversold_v2_reentry_max': 1}


def candles(second_stop):
    bars = []
    for day in DAYS:
        start = datetime.fromisoformat(f'{day}T09:00:00').replace(tzinfo=KST)
        for minute in range(0, 365, 5):
            prices = (100, 100, 100, 100)
            if day == DAYS[0] and minute == 5:
                prices = (100, 100, 97, 97)
            if day == DAYS[0] and 10 <= minute < 30:
                prices = (97, 97, 97, 97)
            if day == DAYS[0] and minute == 30:
                prices = (97, 98, 97, 98)
            if day == DAYS[0] and minute == 35:
                prices = (99, 115, 95 if second_stop else 98, 114)
            bars.append((start + timedelta(minutes=minute), *prices))
    return bars


@pytest.mark.parametrize(('second_stop', 'expected', 'stops'), [(True, -2.2275, 1), (False, 11.1375, 0)])
def test_reentry_waits_for_completed_bar_then_charges_cost_and_checks_stop_first(second_stop, expected, stops):
    delta, bought, repeat_stops = replay_after_stop(candles(second_stop), 100, DAYS, SETTINGS)
    assert delta == pytest.approx(expected)
    assert (bought, repeat_stops) == (1, stops)


def test_missing_session_is_not_counted_as_a_no_trade_case():
    assert replay_after_stop(candles(False)[:73], 100, DAYS, SETTINGS) is None
