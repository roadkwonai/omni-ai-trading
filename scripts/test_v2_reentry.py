import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import v2_reentry

KST = ZoneInfo('Asia/Seoul')
SETTINGS = {'oversold_v2_until_hhmm': 1455, 'oversold_v2_reentry_hold_min': 20, 'oversold_v2_reentry_bounce_pct': 1.0}
held = lambda opened, today: (today - opened).days  # weekdays in these tests


def rows(now, lows, price):
    """Latest 1-minute rows, newest first: one bar per minute ending at `now`, oldest low first in `lows`."""
    out = []
    for n, low in enumerate(reversed(lows)):
        at = now - timedelta(minutes=n)
        out.append({'bsop_time': at.strftime('%H%M00'), 'stck_lwpr': str(low), 'stck_prpr': str(price)})
    return out


def test_stop_watch_then_rebuy_only_after_the_low_held(tmp_path):
    stop = datetime(2026, 11, 18, 10, 0, tzinfo=KST)
    v2_reentry.stopped(tmp_path, '005930', opened='2026-11-17T14:40:00+09:00', hold_days=3, qty=7, reentries=0, limit=2,
                       facts={'drop_pct': -9.0}, now=stop)
    w = v2_reentry.watch(tmp_path)['005930']
    assert (w['qty'], w['reentries'], w['facts']) == (7, 0, {'drop_pct': -9.0})
    now = stop + timedelta(minutes=9)
    # a new low 1 minute ago: not held yet
    assert not v2_reentry.bottom(tmp_path, '005930', rows(now, [100, 99, 98, 97, 96, 95, 94, 93, 92, 91], 91), now, SETTINGS)
    assert v2_reentry.watch(tmp_path)['005930']['low'] == 91
    later = now + timedelta(minutes=21)
    # 21 minutes on, no lower bar and the price 2% off the low: buy again
    assert v2_reentry.bottom(tmp_path, '005930', rows(later, [93] * 10, 93), later, SETTINGS)
    # off the low by less than the bounce: wait
    assert not v2_reentry.bottom(tmp_path, '005930', rows(later, [92] * 10, 91), later, SETTINGS)
    v2_reentry.clear(tmp_path, '005930')
    assert v2_reentry.watch(tmp_path) == {}


def test_used_up_rebuys_are_not_watched_and_the_window_closes(tmp_path):
    v2_reentry.stopped(tmp_path, '005930', opened='2026-11-17T14:40:00+09:00', hold_days=3, qty=7, reentries=2, limit=2)
    assert v2_reentry.watch(tmp_path) == {}
    entry = {'opened': '2026-11-17T14:40:00+09:00', 'hold_days': 3}
    at = lambda d, h, m: datetime(2026, 11, d, h, m, tzinfo=KST)
    assert v2_reentry.open_for(entry, at(18, 10, 0), SETTINGS, held)
    assert not v2_reentry.open_for(entry, at(18, 8, 59), SETTINGS, held)   # before the 09:00 open
    assert not v2_reentry.open_for(entry, at(18, 14, 55), SETTINGS, held)  # the v2 window closed
    assert v2_reentry.open_for(entry, at(19, 13, 59), SETTINGS, held)      # last session before 14:00
    assert not v2_reentry.open_for(entry, at(19, 14, 0), SETTINGS, held)
    assert not v2_reentry.open_for(entry, at(20, 10, 0), SETTINGS, held)   # its sessions are over


def test_live_mirror_sees_the_paper_rebuys(tmp_path):
    import nh_live_follow as follow
    now = datetime(2026, 11, 18, 10, 30, tzinfo=KST)
    lines = [{'time': '2026-11-18T10:25:00+09:00', 'event': 'submitted', 'symbol': '005930', 'source': 'oversold_v2_reentry'},
             {'time': '2026-11-18T10:26:00+09:00', 'event': 'submitted', 'symbol': '000660', 'source': 'oversold_v2'},
             {'time': '2026-11-17T10:25:00+09:00', 'event': 'submitted', 'symbol': '035420', 'source': 'oversold_v2_reentry'}]
    (tmp_path / 'events.jsonl').write_text('\n'.join(json.dumps(x) for x in lines))
    got = follow.paper_reentries(tmp_path / 'orders.sqlite3', now)
    assert list(got) == ['005930'] and got['005930'] == datetime(2026, 11, 18, 10, 25, tzinfo=KST)


def test_scale_max_adds_caps_every_add_path(tmp_path, monkeypatch):
    import sqlite3
    import nh_paper_isolated as p
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE fills (order_id TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
    db.execute('CREATE TABLE decision_order_links (order_id TEXT, cycle_id TEXT)')
    db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)',
                   [(f'o{n}', '114800', 'BUY', 10, 1340, f'2026-11-17T10:0{n}:00+09:00') for n in range(3)])
    monkeypatch.setattr(p, 'SCALE_MAX_ADDS', 2)
    assert p.scale_in_history(db, tmp_path, '114800', 30) is None  # first buy + 2 adds: used up
    monkeypatch.setattr(p, 'SCALE_MAX_ADDS', 5)
    assert p.scale_in_history(db, tmp_path, '114800', 30)


def test_engine_sized_adds_and_partial_sells(monkeypatch):
    import sqlite3
    from decimal import Decimal
    import nh_paper_isolated as p
    monkeypatch.setattr(p, 'ADD_SIZE_PCT', 50)
    assert p.add_size_factor(0.6, 0.6) == 0.5 and p.add_size_factor(0.9, 0.6) == 0.5  # fixed, not by confidence (11/17)
    step = Decimal('0.007')
    # 11/17: same confidence, ask and bid next to the average: not better (the switch: test_nh_paper_isolated add_rule_switch)
    assert p.add_reason(1342, 1343, 1342, 0.80, 0.80, step) is None
    assert p.add_reason(1342, 1343, 1342, 0.83, 0.80, step) == 'confidence_up'   # a stronger decision
    assert p.add_reason(1342, 1332, 1331, 0.80, 0.80, step) == 'cheaper_step'    # a step under the average
    assert p.add_reason(1342, 1352, 1352, 0.80, 0.80, step) == 'profit_step'     # a step in profit
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE fills (symbol TEXT, side TEXT, quantity INTEGER, price REAL)')
    db.execute("INSERT INTO fills VALUES ('114800','BUY',300,1340)")
    monkeypatch.setattr(p, 'ETF7_JEV_SELL_MIN', 0.7)
    monkeypatch.setattr(p, 'PARTIAL_SELL_CONF', 0.55)
    monkeypatch.setattr(p, 'PARTIAL_SELL_PCT', 33)
    assert p.partial_sell_qty(db, '114800', 300, 0.6) == 99
    assert p.partial_sell_qty(db, '114800', 300, 0.5) == 0 and p.partial_sell_qty(db, '114800', 300, 0.7) == 0  # full sale
    db.execute("INSERT INTO fills VALUES ('114800','SELL',99,1335)")
    assert p.partial_sell_qty(db, '114800', 201, 0.6) == 0  # once: the latest fill is that sale


def test_intraday_rebound_buys_only_on_a_market_fall():
    import oversold
    s = {'oversold_market_drop_pct': 3.0}
    assert oversold.market_ok({'market_low_pct': -3.2}, s)
    assert not oversold.market_ok({'market_low_pct': -1.0}, s)   # a lone fall
    assert not oversold.market_ok({'market_low_pct': None}, s)   # market unknown: no
    assert oversold.market_ok({'market_low_pct': -1.0}, {'oversold_market_drop_pct': 0})  # 0 = condition off


def test_add_reason_names_why_an_add_passed(monkeypatch):
    from decimal import Decimal
    import nh_paper_isolated as p
    step = Decimal('0.007')
    assert p.add_reason(1342, 1343, 1342, 0.83, 0.80, step) == 'confidence_up'
    assert p.add_reason(1342, 1332, 1331, 0.80, 0.80, step) == 'cheaper_step'
    assert p.add_reason(1342, 1352, 1352, 0.80, 0.80, step) == 'profit_step'
    assert p.add_reason(1342, 1343, 1342, 0.80, 0.80, step) is None


def test_v2_gate_is_journaled_also_when_it_does_not_pass(tmp_path, monkeypatch):
    import nh_paper_isolated as p
    import oversold
    gate = {'kospi': {'low_pct': -2.7, 'pass': False}, 'kosdaq': {'low_pct': -3.1, 'pass': False}, 'nasdaq_prev': None}
    monkeypatch.setattr(oversold, 'v2_gate', lambda *a, **k: gate)
    monkeypatch.setattr(p, 'exit_plans', lambda root: {})
    logged = []
    monkeypatch.setattr(p, 'journal_event', lambda root, event, **f: logged.append((event, f.get('passed'))))
    p._V2_LOGGED.clear()
    assert p.v2_possible(None, tmp_path, {}) is False
    assert p.v2_possible(None, tmp_path, {}) is False
    assert logged == [('oversold_v2_gate', False)]  # once per state a day


def test_vibe_add_better_returns_the_reason(monkeypatch):
    import sqlite3
    import nh_paper_isolated as p
    import vibe_runner as v
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE buy_confidence (symbol TEXT, time TEXT, confidence REAL)')
    ctx = (None, {'add_step_pct': 0.7}, 1342, None)
    monkeypatch.setattr(p, 'ADD_CONF_SIZING', True)
    assert v.add_better(db, '114800', ctx, 1352, {'bidp': 1352}, 0.8) == 'profit_step'
    assert v.add_better(db, '114800', ctx, 1343, {'bidp': 1342}, 0.8) is None
    monkeypatch.setattr(p, 'ADD_CONF_SIZING', False)
    assert v.add_better(db, '114800', ctx, 1343, {'bidp': 1342}, 0.8) == 'rule_off'
