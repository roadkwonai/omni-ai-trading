from datetime import datetime
from zoneinfo import ZoneInfo

import blocked_outcomes as b

KST = ZoneInfo('Asia/Seoul')


def bar(h, m, close, high=None, low=None):
    """A Yahoo 5-minute bar, stamped at its start: (10, 5) runs 10:05-10:10."""
    return datetime(2026, 11, 18, h, m, tzinfo=KST), close, high or close, low or close, close


def test_every_price_is_a_bar_that_had_closed_by_then():
    """Review 4 C6: the base was the close of the bar running through the event, known only after it."""
    bars = [bar(10, 0, 95.0), bar(10, 5, 100.0, high=130.0), bar(10, 10, 100.0), bar(10, 30, 99.0), bar(10, 35, 102.0),
            bar(14, 55, 101.0), bar(15, 0, 50.0, low=40.0)]
    out = b.settle('2026-11-18T10:07:00+09:00', bars)
    assert out['price'] == 95.0  # 10:00-10:05, not 10:05-10:10
    assert out['r30'] == round((102.0 / 95.0 - 1) * 100, 3)  # 10:37 falls in 10:35-10:40: the first close at or after it
    assert out['r_last'] == round((101.0 / 95.0 - 1) * 100, 3)  # the bar closing at 15:00, not the one starting then
    assert out['mfe'] == round((102.0 / 95.0 - 1) * 100, 3)  # nothing of the bar running through the event
    assert b.settle('2026-11-18T10:05:00+09:00', bars)['price'] == 95.0  # a bar closing exactly then counts


def test_yahoo_bars_try_the_checked_market_then_both_suffixes():
    asked = []

    def fetch(symbol, interval, rng):
        asked.append(symbol)
        return [bar(10, 0, 1.0)] if symbol.endswith('.KQ') else []
    assert b.yahoo_bars('207940', '2026-11-18', fetch) == []  # KOSPI per the case library: .KQ never asked
    assert b.yahoo_bars('900100', '2026-11-18', fetch) == [bar(10, 0, 1.0)]
    assert asked == ['207940.KS', '900100.KS', '900100.KQ']


def test_a_kosdaq_code_from_the_toss_master_is_asked_on_kq_first():
    """11-19 15:26: the issue candidates' 21 KOSDAQ stocks each got a Yahoo 404 on .KS before .KQ answered."""
    asked = []

    def fetch(symbol, interval, rng):
        asked.append(symbol)
        return [bar(10, 0, 1.0)] if symbol.endswith('.KQ') else []
    assert b.yahoo_bars('043260', '2026-11-18', fetch, kosdaq={'043260'}) == [bar(10, 0, 1.0)]
    assert asked == ['043260.KQ']
    import oversold_cases as oc
    assert oc.yahoo_symbols('043260', {'043260'}) == ['043260.KQ'] and oc.yahoo_symbols('207940', {'207940'}) == ['207940.KS']


def test_the_kosdaq_list_is_empty_rather_than_an_error_when_toss_cannot_answer(monkeypatch):
    import oversold_cases as oc
    import toss_data
    monkeypatch.setattr(toss_data, 'configured', lambda: True)
    monkeypatch.setattr(toss_data, 'get', lambda *a, **k: (_ for _ in ()).throw(toss_data.TossError('HTTP 429')))
    assert oc.kosdaq_codes() == set()
    monkeypatch.setattr(toss_data, 'get', lambda path, params=None: [{'symbol': '043260'}, {'symbol': '035760'}])
    assert oc.kosdaq_codes() == {'043260', '035760'}
