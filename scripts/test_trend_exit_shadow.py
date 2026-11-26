from datetime import date, timedelta
from types import SimpleNamespace

from jev_program_paper import clock
from trend_exit_shadow import review

AT = clock(date(2026, 11, 26), '10:00')
POSITION = {'exit_profile': 'trend', 'half_already_sold': True, 'entry_average': 100, 'bid': 106, 'peak_bid': 106}


def market(last):
    bars = {}
    for i, price in enumerate([100, 100, 100, 100, 100, *last]):
        at = AT - timedelta(minutes=10-i)
        opened = price - 1 if i == 5 or (i > 5 and last[i-5] > last[i-6]) else price + 1
        bars[at] = (at, opened, max(price, opened), min(price, opened), price, 200 if i >= 5 else 100)
    return SimpleNamespace(bars=bars)


def test_surge_takes_profit_but_confirmed_trend_keeps_trailing():
    rising = market([101, 102, 103, 104, 106])
    assert review(POSITION, rising, AT, {}, {'bid_qty': 20, 'ask_qty': 100})['action'] == 'review_exit_surge'
    assert review(POSITION, rising, AT, {})['action'] == 'keep_trailing'
    buy = {'status': 'ready', 'direction': 'buy_pressure'}
    result = review(POSITION, rising, AT, buy, {'bid_qty': 120, 'ask_qty': 100})
    assert result['action'] == 'keep_trailing' and result['strong']


def test_weakening_with_selling_takes_remaining_profit():
    falling = market([108, 107, 106, 104, 102])
    result = review({**POSITION, 'bid': 102, 'peak_bid': 108}, falling, AT,
                    {'status': 'ready', 'direction': 'sell_pressure'}, {'bid_qty': 40, 'ask_qty': 100}, 'DOWN')
    assert result['action'] == 'review_exit_weakening'
    assert {'program_sell', 'ask_heavy_book', 'market_news_down'} <= set(result['corroborators'])


def test_only_after_confirmed_partial_profit_and_completed_bars():
    rising = market([101, 102, 103, 104, 106])
    assert review({**POSITION, 'half_already_sold': False}, rising, AT, {})['action'] == 'not_applicable'
    del rising.bars[AT - timedelta(minutes=1)]
    assert review(POSITION, rising, AT, {})['action'] == 'inputs_missing'


def test_slight_volume_drop_or_news_alone_does_not_sell_a_rising_stock():
    rising = market([101, 102, 103, 104, 106])
    for stamp, bar in list(rising.bars.items()):
        rising.bars[stamp] = (*bar[:5], 95 if stamp >= AT - timedelta(minutes=5) else 100)
    assert review(POSITION, rising, AT, {}, news='DOWN')['action'] == 'keep_trailing'
    assert review({**POSITION, 'peak_bid': float('inf')}, rising, AT, {})['action'] == 'inputs_missing'
