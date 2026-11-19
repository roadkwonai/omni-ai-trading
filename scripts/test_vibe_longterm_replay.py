from datetime import date, timedelta

import vibe_longterm_replay as R


def panel(prices):
    start = date(2020, 1, 1)
    return {start + timedelta(days=i): {**{n: p for n in R.PROXY}, 'VIX': 20, 'NDX_USD': p} for i, p in enumerate(prices)}


def test_book_charges_costs_and_tax_on_realized_gains_only():
    book = R.Book(1_000_000)
    book.trade('NDX', 500_000, 100)
    assert round(book.cash) == round(1_000_000 - 500_000 * (1 + R.FEE))
    book.trade('NDX', -10**9, 200)  # sell all at double the price
    gain = book.qty.get('NDX', 0) == 0 and book.tax > 0
    assert gain and abs(book.tax - (500_000 * (1 - R.SLIP) / (1 + R.SLIP) * 2 - 500_000) * R.TAX) < 1


def test_buy_hold_follows_the_price_and_the_reservoir_earns_cash_rate():
    r = R.replay(panel([100] * 5 + [110] * 5), start_weights={'NDX': 1.0})
    assert abs(r['values'][-1] / 100_000_000 - 1.1) < 0.002  # bought at 100 after a day, +10% less costs
    r = R.replay(panel([100] * 10), start_weights={'NDX': 0.0})
    assert r['values'][-1] > 100_000_000  # all cash: interest only


def test_max_drawdown_is_the_deepest_fall_from_a_running_peak():
    assert R.max_drawdown([100, 120, 90, 130, 117]) == -0.25
    assert R.max_drawdown([1, 2, 3]) == 0.0
