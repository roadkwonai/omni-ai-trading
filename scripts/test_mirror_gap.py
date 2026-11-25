import json
import sqlite3
from datetime import datetime

import mirror_gap as m


def fill(at, symbol, side, qty, price, day='2026-11-25'):
    return {'time': datetime.fromisoformat(f'{day}T{at}+09:00'), 'symbol': symbol, 'side': side, 'qty': qty, 'price': float(price)}


PAPER = [fill('10:57:52', '053800', 'BUY', 3, 144_500), fill('11:04:38', '053800', 'SELL', 2, 146_100), fill('11:06:44', '053800', 'SELL', 1, 144_500),
         fill('13:00:00', '005930', 'BUY', 1, 70_000), fill('13:10:00', '005930', 'SELL', 1, 71_000)]  # the live account had stopped
LIVE = [fill('10:58:35', '053800', 'BUY', 3, 145_000), fill('11:04:57', '053800', 'SELL', 2, 144_800), fill('11:10:30', '053800', 'SELL', 1, 142_500)]


def test_each_days_gap_is_split_into_prices_delays_and_what_was_not_followed(tmp_path, monkeypatch):
    (tmp_path / 'live-follow.jsonl').write_text('\n'.join(json.dumps(e) for e in (
        {'time': '2026-11-25T11:09:19+09:00', 'account': 'live-1', 'mode': 'execute', 'action': 'CANCEL_PENDING', 'halted': True},
        {'time': '2026-11-25T11:09:20+09:00', 'account': 'live-2', 'mode': 'execute', 'action': 'CANCEL_PENDING', 'halted': False},
        {'time': '2026-11-25T11:10:00+09:00', 'account': 'live-1', 'mode': 'dry_run', 'action': 'CANCEL_PENDING'})) + '\n{"CANCEL_PENDING"', encoding='utf-8')
    monkeypatch.setattr(m, 'fills', lambda nhplug, key: (PAPER, LIVE))
    [day] = m.gap(tmp_path, 'live-1')
    assert (day['paper_pnl'], day['live_pnl']) == (3200 + 1000, -400 - 2500)
    assert (day['buy_slip'], day['sell_slip']) == (-1500, -2600 - 2000)
    assert day['not_followed_pnl'] == 1000 and (day['buy_delay_s'], day['sell_delay_s']) == (43, 122)
    assert (day['cancelled'], day['halted'], day['orders_paired']) == (1, '11:09', 3)
    assert 0 < day['live_cost'] < day['paper_cost']
    assert m.summary(tmp_path, 'live-1')['halted_days'] == ['2026-11-25']


def test_an_etf_pays_no_sell_tax_and_shares_bought_before_the_record_are_left_out():
    assert m.cost([fill('10:00:00', '069500', 'SELL', 10, 50_000)]) < m.cost([fill('10:00:00', '005930', 'SELL', 10, 50_000)])
    assert m.realized([fill('10:00:00', '005930', 'SELL', 1, 70_000)]) == {}
    assert m.realized([fill('10:00:00', '005930', 'BUY', 1, 70_000), fill('11:00:00', '005930', 'SELL', 2, 71_000)]) == {('2026-11-25', '005930'): 1000}


def test_no_live_fills_is_no_gap(tmp_path):
    assert m.gap(tmp_path, 'live-1') == [] and m.summary(tmp_path, 'live-1') == {}


def test_the_records_are_read_as_they_are_kept(tmp_path):
    import live_fills
    folder = tmp_path / 'shared-etf-paper'
    folder.mkdir()
    with sqlite3.connect(folder / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE fills (order_id, symbol, side, quantity, price, time)')
        db.execute("INSERT INTO fills VALUES ('1','005930','BUY',1,70000,'2026-11-25T10:00:00+09:00')")
    assert live_fills.rows(tmp_path, 'live-1') == []
    assert m.fills(tmp_path, 'live-1')[0] == [fill('10:00:00', '005930', 'BUY', 1, 70_000)]


def test_a_live_stop_just_before_the_papers_is_compared_without_a_delay(tmp_path, monkeypatch):
    paper = [fill('10:04:55', '241710', 'BUY', 3, 125_300), fill('10:21:27', '241710', 'SELL', 3, 123_800)]
    live = [fill('10:05:34', '241710', 'BUY', 3, 124_800), fill('10:20:50', '241710', 'SELL', 3, 123_500)]
    monkeypatch.setattr(m, 'fills', lambda nhplug, key: (paper, live))
    [day] = m.gap(tmp_path, 'live-1')
    assert (day['buy_slip'], day['sell_slip'], day['sell_delay_s']) == (1500, -900, None)
