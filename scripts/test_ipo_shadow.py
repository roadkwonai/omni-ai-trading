import json
from datetime import datetime, timedelta

import ipo_shadow as S

R = dict(start='09:20', cutoff='14:30', flatten='14:40', near_high=3.0, stop=2.0, half=3.0, trail=2.0, time_stop=60,
         daily_max=2, volume=1.2, rising=True, days=2)


def bars(path, volumes=None):
    """[(HH:MM, o, h, l, c, v)] from 09:00, one close per minute; volume grows when the price rises."""
    start = datetime(2026, 11, 18, 9, 0)
    out, prev = [], path[0]
    for k, close in enumerate(path):
        vol = (volumes or [])[k] if volumes else (300 if close > prev else 100)
        out.append(((start + timedelta(minutes=k)).strftime('%H:%M'), prev, max(prev, close), min(prev, close), close, vol))
        prev = close
    return out


# 11-18 에이엘로봇 in miniature: opens 15,100 under a 17,600 offer, climbs, gives back, climbs again
CLIMB = [15100] * 20 + [15100 + 150 * k for k in range(1, 15)] + [17200 - 120 * k for k in range(1, 8)] + \
        [16400 + 70 * k for k in range(1, 15)] + [17380] * 300  # never back to the 17,600 offer


def test_the_offer_price_keeps_the_rule_out_while_a_vwap_base_buys_the_climb():
    day = bars(CLIMB)
    assert S.simulate(day, 17600, 'current', R) == []  # under the offer price all the way to the high
    a = S.simulate(day, 17600, 'vwap_base', R)
    assert a and a[0]['buy'] >= '09:20' and a[0]['why'] in ('trail', 'breakeven', 'stop', 'time_stop', 'flatten')
    assert all(t['buy'] < '14:30' for t in a) and len(a) <= R['daily_max']


def test_the_dip_variant_buys_again_after_a_profitable_exit_above_vwap():
    day = bars(CLIMB)
    a, c = S.simulate(day, 17600, 'vwap_base', R), S.simulate(day, 17600, 'vwap_dip', R)
    assert len(c) >= len(a) and len(c) <= S.DIP_MAX_ENTRIES
    assert c[:1] == a[:1] and c[0]['pct'] > 0  # the first trade is the same breakout, sold at a profit
    assert c[1]['buy'] < a[1]['buy']  # then back in on the pullback above VWAP, before price nears the high again


def test_update_records_each_listing_variant_once_and_only_inside_the_window(tmp_path):
    root = tmp_path
    (root / 'events.jsonl').write_text(json.dumps({'time': '2026-11-19T10:00:00+09:00', 'event': 'ipo_seen', 'symbol': '471800',
                                                   'why': 'below_base', 'price': 15000, 'base': 13830}) + '\n')
    stamps = [int(datetime(2026, 11, d, 9, m, tzinfo=S.KST).timestamp()) for d in (18, 19) for m in range(40)]
    closes = [15000 + 10 * (k % 40) for k in range(80)]
    chart = {'chart': {'result': [{'timestamp': stamps, 'indicators': {'quote': [
        {'open': closes, 'high': closes, 'low': closes, 'close': closes, 'volume': [100] * 80}]}}]}}
    asked = []

    def get(url):
        asked.append(url)
        return chart
    listings = [{'code': '471800', 'name': '에이엘로봇', 'listed': '20261118'}, {'code': '493260', 'name': '옵토닉스', 'listed': '20261110'}]
    import nh_paper_isolated
    from unittest.mock import patch
    with patch.object(S, 'rules', return_value=R), patch.object(nh_paper_isolated, 'IPO_DAYS', 2), \
         patch.object(nh_paper_isolated, 'sessions_held', side_effect=lambda listed, today: (today - listed).days):
        first = S.update(root, '2026-11-19', db=root / 'r.sqlite3', get=get, listings=listings)
        again = S.update(root, '2026-11-19', db=root / 'r.sqlite3', get=get, listings=listings)
    assert first == again == {'day': '2026-11-19', 'rows': 3, 'listings': 1}  # 493260 is past its 2 sessions
    import sqlite3
    with sqlite3.connect(root / 'r.sqlite3') as db:
        rows = db.execute('SELECT variant, session, base FROM ipo_shadow ORDER BY variant').fetchall()
    assert rows == [('current', 1, 13830.0), ('vwap_base', 1, 13830.0), ('vwap_dip', 1, 13830.0)]  # the runner's own base
    assert len(asked) == 2  # one chart request per listing per run
    out = S.summary(root / 'r.sqlite3', '2026-11-19')
    assert set(out) == set(S.VARIANTS) and out['current']['listing_days'] == 1


def test_a_listing_day_without_the_runners_base_has_no_current_row(tmp_path):
    stamps = [int(datetime(2026, 11, 18, 9, m, tzinfo=S.KST).timestamp()) for m in range(40)]
    chart = {'chart': {'result': [{'timestamp': stamps, 'indicators': {'quote': [
        {'open': [100] * 40, 'high': [100] * 40, 'low': [100] * 40, 'close': [100] * 40, 'volume': [1] * 40}]}}]}}
    import nh_paper_isolated
    from unittest.mock import patch
    with patch.object(S, 'rules', return_value=R), patch.object(nh_paper_isolated, 'IPO_DAYS', 2), \
         patch.object(nh_paper_isolated, 'sessions_held', return_value=0):
        out = S.update(tmp_path, '2026-11-18', db=tmp_path / 'r.sqlite3', get=lambda url: chart,
                       listings=[{'code': '471800', 'name': '에이엘로봇', 'listed': '20261118'}])
    assert out['rows'] == 2  # the offer price is known only from the runner's reading


def _chart(days, close=None):
    stamps = [int(datetime(2026, 11, d, 9, m, tzinfo=S.KST).timestamp()) for d in days for m in range(40)]
    closes = [close or 15000 + 10 * (k % 40) for k in range(len(stamps))]
    return {'chart': {'result': [{'timestamp': stamps, 'indicators': {'quote': [
        {'open': closes, 'high': closes, 'low': closes, 'close': closes, 'volume': [100] * len(stamps)}]}}]}}


def test_a_rerun_without_bars_keeps_what_was_recorded(tmp_path):
    """Yahoo keeps 1-minute bars about a week: a later rerun of an old day (or a Yahoo outage) must not wipe it."""
    import nh_paper_isolated
    import sqlite3
    from unittest.mock import patch
    listings = [{'code': '471800', 'name': '에이엘로봇', 'listed': '20261118'}]
    (tmp_path / 'events.jsonl').write_text(json.dumps({'time': '2026-11-19T10:00:00+09:00', 'event': 'ipo_seen', 'symbol': '471800',
                                                       'why': 'below_base', 'price': 15000, 'base': 13830}) + '\n')
    with patch.object(S, 'rules', return_value=R), patch.object(nh_paper_isolated, 'IPO_DAYS', 2), \
         patch.object(nh_paper_isolated, 'sessions_held', side_effect=lambda listed, today: (today - listed).days):
        assert S.update(tmp_path, '2026-11-19', db=tmp_path / 'r.sqlite3', get=lambda url: _chart((18, 19)), listings=listings)['rows'] == 3
        out = S.update(tmp_path, '2026-11-19', db=tmp_path / 'r.sqlite3', get=lambda url: _chart((24, 25)), listings=listings)
    assert out['rows'] == 0
    with sqlite3.connect(tmp_path / 'r.sqlite3') as db:
        assert db.execute("SELECT COUNT(*) FROM ipo_shadow WHERE day = '2026-11-19'").fetchone()[0] == 3


def test_without_the_runners_reading_there_is_no_current_row_after_the_listing_day_either(tmp_path):
    """No source gives the KRX close the runner compares with (11-19 review: Yahoo 1-minute bars end at 14:59, Toss daily
    candles include the NXT after-market), so a guessed base never makes a `current` row."""
    import nh_paper_isolated
    from unittest.mock import patch
    with patch.object(S, 'rules', return_value=R), patch.object(nh_paper_isolated, 'IPO_DAYS', 2), \
         patch.object(nh_paper_isolated, 'sessions_held', side_effect=lambda listed, today: (today - listed).days):
        out = S.update(tmp_path, '2026-11-19', db=tmp_path / 'r.sqlite3', get=lambda url: _chart((18, 19)),
                       listings=[{'code': '471800', 'name': 'x', 'listed': '20261118'}])
    assert out['rows'] == 2


def test_the_window_is_the_saved_ipo_days_not_the_module_default(tmp_path):
    # r5: update() read nh_paper_isolated.IPO_DAYS (2, the default); only the runner loads the dashboard's ipo_days
    import nh_paper_isolated
    from unittest.mock import patch
    listings = [{'code': '471800', 'name': '에이엘로봇', 'listed': '20261117'}]
    asked = []
    with patch.object(S, 'rules', return_value={**R, 'days': 3}), patch.object(nh_paper_isolated, 'IPO_DAYS', 2), \
         patch.object(nh_paper_isolated, 'sessions_held', side_effect=lambda listed, today: (today - listed).days):
        S.update(tmp_path, '2026-11-19', db=tmp_path / 'r.sqlite3', get=lambda url: asked.append(url) or {}, listings=listings)
    assert asked and all('471800' in u for u in asked)  # session 2 of a 3-day window is still looked at
