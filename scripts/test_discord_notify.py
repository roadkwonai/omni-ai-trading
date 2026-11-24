import http.client
import json
import sqlite3
from datetime import datetime

import pytest

import discord_notify as dn
import live_fills

URLS = {'realtime': 'http://rt', 'longterm': 'http://lt'}
TODAY = datetime.now(dn.KST).strftime('%Y-%m-%d')  # fills older than dn.RECENT_DAYS are not alerted


@pytest.fixture(autouse=True)
def no_evening_tasks(monkeypatch):
    """Whatever the hour of the test run, no evening report is expected (the real app store is not read)."""
    monkeypatch.setattr(dn, 'report_runs', lambda: {})


@pytest.fixture(autouse=True)
def _no_holiday_table(monkeypatch):
    """The 15:40 summary asks the KRX holiday table; tests never fetch it."""
    monkeypatch.setattr(dn.L, 'trading_day', lambda day: day.weekday() < 5)


def make_home(tmp_path, monkeypatch):
    monkeypatch.setattr(dn, 'HOME', tmp_path)
    monkeypatch.setattr(dn, 'STATE', tmp_path / 'discord-notify.json')
    monkeypatch.setattr('accounts.FILE', tmp_path / 'accounts.json', raising=False)
    monkeypatch.setattr(dn, 'names_map', lambda: {'005930': '삼성전자'})
    (tmp_path / 'shared-etf-paper').mkdir()
    db = sqlite3.connect(tmp_path / 'shared-etf-paper' / 'orders.sqlite3')
    db.execute('CREATE TABLE fills (order_id TEXT, symbol TEXT, side TEXT, quantity INT, price REAL, time TEXT)')
    db.commit()
    lt = tmp_path / 'vibe-longterm'
    lt.mkdir()
    con = sqlite3.connect(lt / 'longterm.sqlite3')
    con.execute('CREATE TABLE decisions (time TEXT, day TEXT, kind TEXT, regime TEXT, raw_regime TEXT, mode TEXT, modules TEXT, recommended TEXT, indicators TEXT, targets TEXT, orders TEXT, reason TEXT)')
    con.execute('CREATE TABLE nav (day TEXT, book TEXT, value REAL, PRIMARY KEY (day, book))')
    con.commit()
    return db, con


def sent_log():
    log = []

    def sender(url, text):
        log.append((url, text))
        return True, 0, False
    return log, sender


def run(tmp_path, sender, **kw):
    return dn.run_once(URLS, sender=sender, sleep=lambda s: None, documents=None, **kw)


def test_first_run_sets_cursor_without_sending(tmp_path, monkeypatch):
    db, _ = make_home(tmp_path, monkeypatch)
    db.execute("INSERT INTO fills VALUES ('o1','005930','BUY',1,70000,'2026-11-17T10:00:00+09:00')")
    db.commit()
    log, sender = sent_log()
    assert run(tmp_path, sender) == [] and log == []
    assert run(tmp_path, sender) == [] and log == []  # old history stays quiet


def test_paper_fill_goes_to_realtime_only(tmp_path, monkeypatch):
    db, _ = make_home(tmp_path, monkeypatch)
    log, sender = sent_log()
    run(tmp_path, sender)
    db.execute(f"INSERT INTO fills VALUES ('o2','005930','BUY',10,71200,'{TODAY}T10:31:00+09:00')")
    db.commit()
    run(tmp_path, sender)
    assert [u for u, _ in log] == ['http://rt']
    assert '삼성전자(005930) 10주 × 71,200원' in log[0][1] and '10:31' in log[0][1]
    run(tmp_path, sender)
    assert len(log) == 1  # exactly once


def test_longterm_decision_goes_to_longterm_only(tmp_path, monkeypatch):
    _, con = make_home(tmp_path, monkeypatch)
    log, sender = sent_log()
    run(tmp_path, sender)
    con.execute("INSERT INTO decisions VALUES ('t','2026-11-17','regime','correction','correction','manual','[\"bogle_core\"]','[]','{}','{}','[]','근거')")
    con.commit()
    run(tmp_path, sender)
    assert [u for u, _ in log] == ['http://lt'] and '조정' in log[0][1]


def test_failed_send_retried_not_duplicated(tmp_path, monkeypatch):
    db, _ = make_home(tmp_path, monkeypatch)
    log, sender = sent_log()
    run(tmp_path, sender)
    db.execute(f"INSERT INTO fills VALUES ('o3','005930','SELL',5,72000,'{TODAY}T11:00:00+09:00')")
    db.commit()
    run(tmp_path, lambda u, t: (False, 0, False))  # network down
    assert json.loads((tmp_path / 'discord-notify.json').read_text())['fail']
    run(tmp_path, sender)
    run(tmp_path, sender)
    assert len(log) == 1 and '매도' in log[0][1]


def test_permanent_4xx_skipped_after_three_tries(tmp_path, monkeypatch):
    db, _ = make_home(tmp_path, monkeypatch)
    run(tmp_path, sent_log()[1])
    db.execute(f"INSERT INTO fills VALUES ('o4','005930','BUY',1,1000,'{TODAY}T11:00:00+09:00')")
    db.commit()
    for _ in range(3):
        run(tmp_path, lambda u, t: (False, 0, True))
    log, sender = sent_log()
    run(tmp_path, sender)
    assert log == []


def test_split_long_text():
    pieces = dn.split_text('\n'.join(['가' * 100] * 60))
    assert len(pieces) > 1 and all(len(p) <= dn.LIMIT for p in pieces)
    assert dn.split_text('x' * 5000)[0] == 'x' * dn.LIMIT


def test_429_waits_then_retries():
    calls, waits = [], []

    def sender(url, text):
        calls.append(1)
        return (True, 0, False) if len(calls) == 2 else (False, 1.5, False)
    assert dn.send_all('u', ['hi'], sender, waits.append) == (True, False)
    assert waits == [1.5]


def test_longterm_messages_use_the_engines_labels_and_post_dca_orders(tmp_path, monkeypatch):
    from datetime import datetime
    _, con = make_home(tmp_path, monkeypatch)
    log, sender = sent_log()
    run(tmp_path, sender)
    rows = [('t', '2026-11-18', 'regime', r, r, 'manual', '["bogle_core"]', '[]', '{}', '{}', '[]', '근거')
            for r in ('panic', 'bear', 'overheat', 'bull')]
    order = [{'key': 'NDX', 'code': '379810', 'side': 'BUY', 'qty': 30, 'price': 20000, 'status': 'ok', 'filled': 30}]
    rows.append(('t', '2026-11-18', 'dca', 'bull', None, 'manual', None, None, None, None, json.dumps(order), '정기 적립 600,000원'))
    con.executemany('INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', rows)
    con.executemany('INSERT INTO nav VALUES (?,?,?)', [('2026-11-17', 'actual', 1.0), ('2026-11-18', 'actual', 100_500_000),
                                                       ('2026-11-18', 'contributed', 600_000)])
    con.commit()

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 18, 15, 41, tzinfo=dn.KST)
    monkeypatch.setattr(dn, 'datetime', Clock)
    run(tmp_path, sender)
    texts = [t for url, t in log if url == URLS['longterm']]
    assert [label in t for label, t in zip(('투매', '약세', '과열', '강세 안정'), texts)] == [True] * 4
    assert all('모드 수동' in t for t in texts[:4])
    assert '적립식' in texts[4] and '379810 30주' in texts[4]
    assert texts[5] == '📈 2026-11-18 장기투자 가상 NAV · 실제(가상 계좌) 100,500,000원 · 적립 누계 600,000원'


def balance_rows(path, day, values):
    path.write_text(''.join(json.dumps({'time': f'{day}T{t}:00+09:00', 'total_assets': v}) + '\n' for t, v in values))


def test_sell_says_profit_and_today_totals(tmp_path, monkeypatch):
    db, _ = make_home(tmp_path, monkeypatch)
    log, sender = sent_log()
    run(tmp_path, sender)
    balance_rows(tmp_path / 'shared-etf-paper' / 'account-snapshots.jsonl', TODAY, [('09:05', 1000000), ('10:35', 1010000)])
    db.execute(f"INSERT INTO fills VALUES ('b1','005930','BUY',10,70000,'{TODAY}T10:00:00+09:00')")
    db.execute(f"INSERT INTO fills VALUES ('s1','005930','SELL',10,71000,'{TODAY}T10:31:00+09:00')")
    db.commit()
    run(tmp_path, sender)
    buy, sell = log[0][1], log[1][1]
    assert '오늘 실현' not in buy  # buys carry no day line
    assert sell.startswith('💰 전량 매도') and '실현손익 **+10,000원 (+1.43%)**' in sell  # the whole position sold at a profit
    assert '오늘 실현 +10,000원 (+1.43%)' in sell and '총자산 1,010,000원 (오늘 +10,000원 +1.00%, 10:35 기준)' in sell


def test_close_summary_lists_every_account(tmp_path, monkeypatch):
    from datetime import datetime as real
    db, _ = make_home(tmp_path, monkeypatch)
    (tmp_path / 'accounts.json').write_text(json.dumps({'accounts': {
        'live-1': {'kind': 'live', 'label': '실계좌 1'}, 'live-2': {'kind': 'live', 'label': '실계좌 2'}}}))
    day = '2026-11-17'
    balance_rows(tmp_path / 'shared-etf-paper' / 'account-snapshots.jsonl', day, [('09:05', 1000000), ('15:20', 990000)])
    balance_rows(tmp_path / 'live-balance-live-1.jsonl', day, [('09:10', 500000), ('15:10', 505000)])
    balance_rows(tmp_path / 'live-balance-live-2.jsonl', day, [('09:40', 500000), ('14:40', 495000)])
    from live_fills import SCHEMA, NAME
    with sqlite3.connect(tmp_path / NAME) as live:
        live.execute(SCHEMA)
        live.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?)', [
            ('live-1', day, 1, '005930', 'BUY', 2, 70000, f'{day}T10:00:00+09:00', 'x'),
            ('live-1', day, 2, '005930', 'SELL', 2, 72500, f'{day}T11:00:00+09:00', 'x')])

    class Now(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 11, 17, 15, 41, tzinfo=tz)
    monkeypatch.setattr(dn, 'datetime', Now)
    import accounts
    (text,) = [t for _, _, t in dn.summary_item(set(), accounts.load(tmp_path / 'accounts.json'))]
    assert text.startswith('🏁 2026-11-17 마감 요약')
    assert '**모의(Jev)**\n　오늘 실현 +0원 · 총자산 990,000원 (오늘 -10,000원 -1.00%, 15:20 기준)' in text
    assert '**실계좌 1** · 청산 1건 · 승 1/패 0\n　오늘 실현 +5,000원 (+3.57%) · 총자산 505,000원 (오늘 +5,000원 +1.00%' in text
    assert '**실계좌 2**' in text and '총자산 495,000원 (오늘 -5,000원 -1.00%' in text
    assert '**실계좌 합계**\n　오늘 실현 +5,000원 (+3.57%) · 총자산 1,000,000원 (오늘 +0원' in text


def test_a_long_term_step_error_or_miss_goes_to_the_long_term_channel(tmp_path, monkeypatch):
    _, con = make_home(tmp_path, monkeypatch)
    log, sender = sent_log()
    run(tmp_path, sender)
    con.executemany('INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    [('t', '2026-11-18', 'error', None, None, None, None, None, None, None, None, 'dca: error:ValueError:quote unavailable'),
                     ('t', '2026-11-18', 'missed', None, None, None, None, None, None, None, None, 'trade: 09:40~14:30 안에 끝나지 않음'),
                     ('t', '2026-11-18', 'map', None, None, None, None, None, '{}', None, None, '모듈별 영향')])
    con.commit()
    run(tmp_path, sender)
    texts = [t for url, t in log if url == URLS['longterm']]
    assert len(texts) == 2 and '단계 오류' in texts[0] and 'quote unavailable' in texts[0] and '시간 놓침' in texts[1]


def test_a_long_term_order_alert_carries_its_evidence_summary(tmp_path, monkeypatch):
    _, con = make_home(tmp_path, monkeypatch)
    log, sender = sent_log()
    run(tmp_path, sender)
    order = [{'key': 'NDX', 'code': '379810', 'side': 'BUY', 'qty': 30, 'price': 20000, 'status': 'ok', 'filled': 30}]
    con.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                ('2026-11-18T12:02:35+09:00', '2026-11-18', 'order', 'correction', 'correction', 'manual', '[]', '[]', '{}', '{}',
                 json.dumps(order), '초기 투입'))
    con.execute('CREATE TABLE IF NOT EXISTS evidence (time TEXT, day TEXT, book TEXT, kind TEXT, key TEXT, code TEXT, side TEXT, qty INTEGER, '
                'price REAL, number INTEGER, filled INTEGER, fill_price REAL, why TEXT, masters TEXT, reconstructed INTEGER, summary TEXT, bundle TEXT)')
    con.execute("INSERT INTO evidence VALUES ('2026-11-18T12:02:35+09:00','2026-11-18','actual','order','NDX','379810','BUY',30,20000,1,30,20000,"
                "'initial_deployment','[]',0,'KODEX 미국나스닥100 매수 30주: 초기 투입 — 12주 분할','{}')")
    con.commit()
    run(tmp_path, sender)
    text = [t for url, t in log if url == URLS['longterm']][0]
    assert '근거: KODEX 미국나스닥100 매수 30주: 초기 투입 — 12주 분할' in text and '왜 샀나' in text


def test_launchd_pythonpath_can_import_engine_compare(tmp_path):
    # launchd runs discord-notify with PYTHONPATH=agent:scripts only; without streamlit_namuh the P&L import failed silently
    import os
    import subprocess
    import sys
    from pathlib import Path
    env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(tmp_path), 'VIBE_TRADING_HOME': str(tmp_path),
           'PYTHONPATH': 'agent:scripts'}
    result = subprocess.run([sys.executable, '-c', 'import discord_notify, engine_compare; assert callable(engine_compare.journal)'],
                            cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-1500:]


def test_a_failed_longterm_send_is_collected_again_and_a_sent_one_is_not(tmp_path, monkeypatch):
    # review 2026-11-18: the cursor moved past a row before it was sent, so a failed error alert was lost for good
    import sqlite3
    monkeypatch.setattr(dn, 'HOME', tmp_path)
    (tmp_path / 'vibe-longterm').mkdir()
    db = sqlite3.connect(tmp_path / 'vibe-longterm' / 'longterm.sqlite3')
    db.execute('CREATE TABLE decisions (time,day,kind,regime,raw_regime,mode,modules,recommended,indicators,targets,orders,reason)')
    db.execute('CREATE TABLE nav (day, book, value)')
    for reason in ('boom', 'bang'):
        db.execute("INSERT INTO decisions VALUES ('2026-11-18T09:40:00+09:00','2026-11-18','error',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,?)", (reason,))
    db.commit()
    db.close()
    state = {'seen': [], 'lt_cursor': 0}
    items, state['lt_cursor'] = dn.longterm_items(set(), 0, {})
    dn.deliver(items, state, {'longterm': 'http://x'}, sender=lambda url, text, timeout=10: (False, 0, False), sleep=lambda s: None)
    again, _ = dn.longterm_items(set(state['seen']), state['lt_cursor'], {})
    assert [i[1] for i in again] == ['lt:1', 'lt:2']
    calls = []
    dn.deliver(again[:1], state, {'longterm': 'http://x'}, sender=lambda url, text, timeout=10: (calls.append(text), (True, 0, False))[1],
               sleep=lambda s: None)
    left, _ = dn.longterm_items(set(state['seen']), state['lt_cursor'], {})
    assert [i[1] for i in left] == ['lt:2'] and len(calls) == 1


def test_a_live_plan_made_live_after_a_dry_one_and_the_days_dca_are_both_posted(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(dn, 'HOME', tmp_path)
    order = {'side': 'BUY', 'code': '379810', 'qty': 1}
    events = [{'time': '2026-11-19T09:40:00+09:00', 'account': 'live-2', 'action': 'PLAN', 'execution': 'DRY_RUN', 'orders': [order]},
              {'time': '2026-11-19T10:05:00+09:00', 'account': 'live-2', 'action': 'PLAN', 'execution': 'LIVE', 'orders': [order]},
              {'time': '2026-11-19T10:10:00+09:00', 'account': 'live-2', 'action': 'DCA', 'execution': 'LIVE', 'orders': [order]},
              {'time': '2026-11-19T10:11:00+09:00', 'account': 'live-2', 'action': 'PLAN', 'execution': 'LIVE', 'orders': [order]}]
    (tmp_path / 'longterm-live.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
    items = dn.plan_items(set())
    assert len(items) == 3 and items[2][2].startswith('💰 실계좌 적립식 주문')


# ---------- review 4 (2026-11-19): duplicate and lost alerts ----------
LIVE1 = {'live-1': {'kind': 'live', 'engine': 'jev', 'label': '실계좌 1'}}
DAYN = TODAY.replace('-', '')


def live_home(tmp_path, monkeypatch, accounts):
    db, _ = make_home(tmp_path, monkeypatch)
    monkeypatch.setattr('accounts.load', lambda *a, **k: dict(accounts))
    with sqlite3.connect(tmp_path / live_fills.NAME) as con:
        con.execute(live_fills.SCHEMA)
    return db


def live_fill(tmp_path, number, key='live-1'):
    with sqlite3.connect(tmp_path / live_fills.NAME) as con:  # NH gives no clock: stored at T00:00:00
        con.execute('INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?)', (key, DAYN, number, '114800', 'BUY', 1, 1350, f'{TODAY}T00:00:00+09:00', 'x'))


def guard_links(tmp_path, numbers):
    """The executor log + guard DB that resolve a stored T00:00:00 fill to its submission time."""
    (tmp_path / 'live-follow.jsonl').write_text(''.join(
        json.dumps({'time': f'{TODAY}T10:{i:02d}:00+09:00', 'account': 'live-1', 'intent': f'fp{n}'}) + '\n' for i, n in enumerate(numbers)))
    with sqlite3.connect(tmp_path / 'live-orders.sqlite3') as con:
        con.execute('CREATE TABLE IF NOT EXISTS live_intents (day TEXT, fingerprint TEXT, order_number TEXT)')
        con.executemany('INSERT INTO live_intents VALUES (?,?,?)', [(DAYN, f'fp{n}', str(n)) for n in numbers])


def test_a_live_fill_is_alerted_once_when_its_time_gets_resolved(tmp_path, monkeypatch):
    live_home(tmp_path, monkeypatch, LIVE1)
    live_fill(tmp_path, 385827)
    log, sender = sent_log()
    run(tmp_path, sender)  # baseline
    live_fill(tmp_path, 392487)  # the guard link is not written yet (or the guard DB is busy)
    run(tmp_path, sender)
    assert len(log) == 1
    guard_links(tmp_path, (385827, 392487))  # now both resolve to their submission time
    run(tmp_path, sender)
    assert len(log) == 1


def test_one_unreadable_guard_db_pass_does_not_repost_the_live_history(tmp_path, monkeypatch):
    live_home(tmp_path, monkeypatch, LIVE1)
    for n in (1, 2, 3):
        live_fill(tmp_path, n)
    guard_links(tmp_path, (1, 2, 3))
    log, sender = sent_log()
    run(tmp_path, sender)
    (tmp_path / 'live-orders.sqlite3').rename(tmp_path / 'moved.sqlite3')  # 'database is locked' past the timeout
    run(tmp_path, sender)
    assert log == []


def test_live_ids_seen_before_the_fix_are_read_in_the_new_form(tmp_path, monkeypatch):
    live_home(tmp_path, monkeypatch, LIVE1)
    live_fill(tmp_path, 7)
    guard_links(tmp_path, (7,))
    # today's 15:40 summary is marked sent: this test is about the fill id only, whatever the wall clock (11-19 16:3x)
    (tmp_path / 'discord-notify.json').write_text(json.dumps({'seen': [f'live:live-1:{TODAY}T00:00:00+09:00:7', f'summary:{TODAY}'],
                                                               'lt_cursor': 0}))
    log, sender = sent_log()
    run(tmp_path, sender)
    assert log == []


def test_a_second_jev_live_account_gets_its_fill_alerts(tmp_path, monkeypatch):
    live_home(tmp_path, monkeypatch, {'live-2': {'kind': 'live', 'engine': 'jev', 'label': '실계좌 2'}})
    log, sender = sent_log()
    run(tmp_path, sender)
    live_fill(tmp_path, 100001, 'live-2')
    run(tmp_path, sender)
    assert [u for u, _ in log] == ['http://rt'] and '실계좌 2' in log[0][1]


def test_old_fills_are_not_reposted_once_seen_overflows(tmp_path, monkeypatch):
    db, _ = make_home(tmp_path, monkeypatch)
    monkeypatch.setattr(dn, 'SEEN_KEEP', 5)
    db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)',
                   [(f'old{i}', '069500', 'BUY', 1, 40000, f'2026-08-0{i + 1}T10:00:00+09:00') for i in range(5)])
    db.commit()
    log, sender = sent_log()
    run(tmp_path, sender)
    db.execute(f"INSERT INTO fills VALUES ('new1','069500','BUY',1,40000,'{TODAY}T10:00:00+09:00')")
    db.commit()
    for _ in range(3):
        run(tmp_path, sender)
    assert len(log) == 1


def test_a_pass_killed_mid_delivery_keeps_what_it_already_sent(tmp_path, monkeypatch):
    db, _ = make_home(tmp_path, monkeypatch)
    log, sender = sent_log()
    run(tmp_path, sender)
    db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', [(f'k{i}', '005930', 'BUY', 1, 70000, f'{TODAY}T10:0{i}:00+09:00') for i in range(3)])
    db.commit()
    tried = []

    def killed_on_the_third(url, text):
        tried.append(text)
        if len(tried) == 3:
            raise RuntimeError('launchd stopped the watcher')
        return True, 0, False
    with pytest.raises(RuntimeError):
        run(tmp_path, killed_on_the_third)
    run(tmp_path, sender)
    assert [t for _, t in log] == [tried[2]]  # only the one that never went out


def test_post_reports_a_broken_reply_or_url_as_a_failed_send(monkeypatch):
    def broken(request, timeout=10):
        raise http.client.BadStatusLine('')
    monkeypatch.setattr(dn.urllib.request, 'urlopen', broken)
    assert dn.post('http://rt', 'x') == (False, 0, False)
    assert dn.post('not a webhook url', 'x') == (False, 0, False)


def test_no_close_summary_on_a_krx_holiday(tmp_path, monkeypatch):
    from datetime import datetime as real
    make_home(tmp_path, monkeypatch)

    class Now(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 12, 25, 15, 41, tzinfo=tz)  # a Friday, KRX closed
    monkeypatch.setattr(dn, 'datetime', Now)
    monkeypatch.setattr(dn.L, 'trading_day', lambda day: day != Now.now().date())
    assert dn.summary_item(set(), {}) == []



def test_sell_head_says_stop_only_on_a_loss_and_full_when_everything_is_sold():
    """user 2026-11-19: 손절 only for a loss taken by a stop; selling the whole position is 전량 매도."""
    row = lambda qty: {'symbol': '005930', 'side': 'SELL', 'qty': qty, 'price': 71000, 'time': f'{TODAY}T10:31:00+09:00'}  # noqa: E731
    trail = dn.fill_line(row(10), {}, '모의', 'trailing_stop', 5000, 0.7, closed=True)
    assert trail.startswith('💰 전량 매도') and '손절' not in trail and '추적 청산' in trail
    assert dn.fill_line(row(10), {}, '모의', 'stop_loss', -9000, -1.9, closed=True).startswith('🛑 손절')
    assert dn.fill_line(row(5), {}, '모의', 'stop_loss', -4000, -1.9).startswith('🛑 손절')
    assert dn.fill_line(row(5), {}, '모의', 'take_profit', 3000, 1.0).startswith('💰 익절')
    flat = dn.fill_line(row(10), {}, '모의', 'session_flatten', -2000, -0.3, closed=True)
    assert flat.startswith('🔴 전량 매도') and '장 마감 정리' in flat
    even = dn.fill_line(row(4), {}, '모의', 'breakeven_stop', 0, 0.0)
    assert even.startswith('🔴 매도') and '손절' not in even and '본전 청산' in even
    assert dn.fill_line(row(4), {}, '실계좌 1', 'stop_loss', None, None).startswith('🔴 매도')  # P&L unknown: no 손절 claim


def test_a_live_accounts_own_stop_at_a_loss_says_stop(tmp_path, monkeypatch):
    """review5: the mirror's own stop (follow_exits, no paper sell to name it) sold at a loss is '🛑 손절', not '전량 매도'."""
    monkeypatch.setattr(dn, 'HOME', tmp_path)
    db = sqlite3.connect(tmp_path / 'live-orders.sqlite3')
    db.execute('CREATE TABLE live_intents (scope TEXT, day TEXT, symbol TEXT, order_number INT)')
    db.execute('CREATE TABLE follow_exits (scope TEXT, day TEXT, symbol TEXT, reason TEXT)')
    day = TODAY.replace('-', '')
    db.execute('INSERT INTO live_intents VALUES (?,?,?,?)', ('s', day, '005930', 2))
    db.execute('INSERT INTO follow_exits VALUES (?,?,?,?)', ('s', day, '005930', '2% stop from average price at x'))
    db.commit()
    fills = [{'time': f'{TODAY}T09:30:00+09:00', 'symbol': '005930', 'side': 'BUY', 'qty': 10, 'price': 100_000, 'order_no': 1},
             {'time': f'{TODAY}T10:10:00+09:00', 'symbol': '005930', 'side': 'SELL', 'qty': 10, 'price': 97_900, 'order_no': 2}]
    monkeypatch.setattr(live_fills, 'rows', lambda *_a, **_k: fills)
    monkeypatch.setattr(dn, 'paper_exits', lambda: [])
    monkeypatch.setattr(dn, 'day_status', lambda *a, **k: '')
    items = dn.live_items({'live-1': {'kind': 'live', 'engine': 'jev', 'label': '실계좌 1'}}, {}, set())
    sell = [t for _, i, t in items if i.endswith(':2')][0]
    assert sell.startswith('🛑 손절') and '실계좌 손절' in sell


# ---------- Telegram · Slack (user 2026-11-19) ----------
def test_env_builds_every_destination_per_channel():
    values = {'DISCORD_WEBHOOK_REALTIME': 'https://d/rt', 'SLACK_WEBHOOK_REALTIME': 'https://s/rt',
              'TELEGRAM_BOT_TOKEN': 'tok', 'TELEGRAM_CHAT_REALTIME': '-100', 'TELEGRAM_CHAT_LONGTERM': '-200'}
    out = dn.urls_from_env(values)
    assert out['realtime'] == [('discord', 'https://d/rt'), ('telegram', ('tok', '-100')), ('slack', 'https://s/rt')]
    assert out['longterm'] == [('telegram', ('tok', '-200'))]
    assert dn.urls_from_env({'TELEGRAM_CHAT_REALTIME': '-100'})['realtime'] == []  # a chat without the bot token is off


def test_render_per_destination():
    text = '**모의** 매수 [리포트](https://x.io/a?b=1&c=2) 1<2'
    assert dn.render('discord', text) == text
    assert dn.render('telegram', text) == '모의 매수 리포트 (https://x.io/a?b=1&c=2) 1<2'
    assert dn.render('slack', text) == '*모의* 매수 <https://x.io/a?b=1&amp;c=2|리포트> 1&lt;2'


def test_each_destination_keeps_its_own_sent_list(monkeypatch):
    calls = []
    monkeypatch.setitem(dn.POSTERS, 'slack', lambda url, text, timeout=10: (calls.append(('slack', text)), (True, 0, False))[1])
    fails = {'n': 0}

    def telegram(address, text, timeout=10):
        fails['n'] += 1
        calls.append(('telegram', text))
        return (False, 0, False) if fails['n'] == 1 else (True, 0, False)
    monkeypatch.setitem(dn.POSTERS, 'telegram', telegram)
    discord = lambda url, text, timeout=10: (calls.append(('discord', text)), (True, 0, False))[1]
    urls = {'realtime': [('discord', 'u'), ('telegram', ('t', 'c')), ('slack', 's')]}
    state = {'seen': ['old']}
    dn.deliver([('realtime', 'a', 'A')], state, urls, sender=discord, sleep=lambda s: None)
    assert state['seen'] == ['old', 'a'] and state['seen_slack'] == ['old', 'a'] and state['seen_telegram'] == ['old']
    assert dn.done_ids(state, urls) == {'old'}  # Telegram still owes 'a', so collect brings it back
    calls.clear()
    dn.deliver([('realtime', 'a', 'A')], state, urls, sender=discord, sleep=lambda s: None)
    assert calls == [('telegram', 'A')]  # only the destination that missed it
    assert dn.done_ids(state, urls) == {'old', 'a'}


def test_new_destination_does_not_replay_history():
    state = {'seen': ['x', 'y']}
    lists = dn.seen_lists(state, {'realtime': [('slack', 's')]})
    assert lists['slack'] == ['x', 'y'] and state['seen_slack'] == ['x', 'y']


def test_long_term_cursor_waits_for_every_destination(monkeypatch):
    monkeypatch.setitem(dn.POSTERS, 'slack', lambda url, text, timeout=10: (False, 0, False))
    ok = lambda url, text, timeout=10: (True, 0, False)
    state = {'seen': [], 'lt_cursor': 10}
    dn.deliver([('longterm', 'lt:7', 'L')], state, {'longterm': [('discord', 'u'), ('slack', 's')]}, sender=ok, sleep=lambda s: None)
    assert state['lt_cursor'] == 6 and 'lt:7' in state['seen'] and 'lt:7' not in state['seen_slack']


def test_a_dca_alert_is_posted_once_a_day_with_what_to_buy(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(dn, 'HOME', tmp_path)
    order = {'key': 'NDX', 'code': '379810', 'name': 'KODEX 미국나스닥100', 'krw': 58_800, 'price': 23_315, 'qty': 2}
    events = [{'time': '2026-11-23T10:00:00+09:00', 'account': 'live-2', 'action': 'DCA_ALERT', 'orders': [order], 'notes': ['여운봉 · 조정']},
              {'time': '2026-11-23T10:05:00+09:00', 'account': 'live-2', 'action': 'DCA_ALERT', 'orders': [order], 'notes': []}]
    (tmp_path / 'longterm-live.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
    items = dn.plan_items(set())
    assert len(items) == 1 and items[0][2].startswith('🔔 적립 알림')
    assert '379810' in items[0][2] and '23,315' in items[0][2] and '2주' in items[0][2] and '주문 없음' in items[0][2]


# ---- 2026-11-24 복기 제안 (A)4: a report task that started but wrote nothing is told the same evening ----

def evening(tmp_path, monkeypatch, hhmm, tasks, day='2026-11-24'):
    monkeypatch.setattr(dn, 'datetime', type('D', (datetime,), {'now': classmethod(lambda cls, tz=None: datetime.fromisoformat(f'{day}T{hhmm}:00+09:00'))}))
    monkeypatch.setattr(dn, 'HOME', tmp_path)
    monkeypatch.setattr(dn.L, 'trading_day', lambda d: True)
    monkeypatch.setattr(dn, 'report_runs', lambda: tasks)
    return dn.missing_reports(set())


def test_an_evening_report_that_was_not_written_is_told_once(tmp_path, monkeypatch):
    run = lambda at=None, on=True: {'enabled': on, 'last': datetime.fromisoformat(at) if at else None}  # noqa: E731
    tasks = {'longterm-review-daily': run('2026-11-24T16:55:19+09:00'), 'trading-journal': run('2026-11-24T16:46:00+09:00'), 'trading-daily-improvement': run(on=False)}
    assert evening(tmp_path, monkeypatch, '17:30', tasks) == []
    reviews = tmp_path / 'shared-etf-paper/reviews'
    reviews.mkdir(parents=True)
    (reviews / 'journal-2026-11-24.md').write_text('x')
    got = evening(tmp_path, monkeypatch, '18:05', tasks)
    assert [(c, i) for c, i, _ in got] == [('longterm', 'missing:longterm-review-daily:2026-11-24')]
    assert '16:55에 시작' in got[0][2] and '승인 대기' in got[0][2]
    (tmp_path / 'vibe-longterm/reviews').mkdir(parents=True)
    (tmp_path / 'vibe-longterm/reviews/audit-2026-11-24.md').write_text('x')
    assert evening(tmp_path, monkeypatch, '18:05', tasks) == []


def test_a_task_that_never_started_and_an_unknown_store_are_told_apart(tmp_path, monkeypatch):
    run = {'enabled': True, 'last': datetime.fromisoformat('2026-11-23T16:55:00+09:00')}
    assert '오늘 시작하지 않음' in evening(tmp_path, monkeypatch, '18:05', {'longterm-review-daily': run})[0][2]
    got = evening(tmp_path, monkeypatch, '18:05', None)  # the app's store could not be read: the files alone are judged
    assert len(got) == 3 and all('시작' not in text for _, _, text in got)


def test_a_weekly_report_is_expected_on_friday_only(tmp_path, monkeypatch):
    run = {'enabled': True, 'last': None}
    tasks = {'longterm-audit-weekly': run, 'longterm-strategy-weekly': run, 'oversold-weekly-audit': run}
    assert evening(tmp_path, monkeypatch, '19:05', tasks) == []
    assert {i.split(':')[1] for _, i, _ in evening(tmp_path, monkeypatch, '19:05', tasks, day='2026-11-27')} == set(tasks)
