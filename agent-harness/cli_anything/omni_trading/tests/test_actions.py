"""Requests behind the person's code: nothing is filed, started or sent without it, and never twice."""
import dataclasses
import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from cli_anything.omni_trading.core import actions as X
from cli_anything.omni_trading.core import approval as AP
from cli_anything.omni_trading.core.output import KST, OmniError, Refused

from .conftest import account, write
from .test_core import Box
from .test_more import data, run

UP = {'registered': True, 'running': True, 'pid': 1, 'last_exit': 0, 'started': None}


@pytest.fixture
def day(home, monkeypatch):
    """A session: the runner and the mirror run, the paper holds 10 shares of 069500."""
    import os_services
    state = {'up': True}
    monkeypatch.setattr(os_services, 'state', lambda name, **k: dict(UP, running=state['up']))
    monkeypatch.setattr(os_services, 'states', lambda **k: {})
    held(home, 10)
    return home, state


def held(nh, qty, minutes=0):
    (nh / 'shared-etf-paper/account-state.json').write_text(json.dumps(
        {'time': (datetime.now(KST) - timedelta(minutes=minutes)).isoformat(), 'holdings': {'069500': {'rsdl_qty': qty, 'iem_nm': 'KODEX 200'}}}))


def requests(nh, name='exit-requests.json'):
    path = nh / 'shared-etf-paper' / name
    return json.loads(path.read_text()) if path.exists() else None


def swap(monkeypatch, action, run):
    monkeypatch.setitem(X.ACTIONS, action, dataclasses.replace(X.ACTIONS[action], run=run))


def go(nh, action, phrase=None, **args):
    made, box = AP.plan_action(nh, action, '시험', **args), Box()
    with pytest.raises(OmniError) as caught:
        AP.apply(nh, made['plan'])
    assert caught.value.code == 3 and made['level'] == 'H'
    AP.approve(nh, made['plan'], box)
    return AP.apply(nh, made['plan'], box.code, phrase=phrase, sender=box), box


@pytest.fixture
def plain(day):
    return day[0]


def test_a_paper_sale_is_filed_only_with_the_code(plain):
    made = AP.plan_action(plain, 'paper_sell', '시험', code='069500', qty=4)
    assert made['affects'] == ['live-1'] and '따라' in made['how'][0] and requests(plain) is None
    with pytest.raises(OmniError):
        AP.apply(plain, made['plan'], 'AAAAAAAA')
    assert requests(plain) is None
    out, box = go(plain, 'paper_sell', code='069500', qty=4)
    assert requests(plain) == {'069500': 4} and out['filed'] and '모의 매도 요청' in box[0] and '4주' in box[0] and '적용됨' in box[1]
    with pytest.raises(Refused, match='이미 처리'):
        AP.apply(plain, out['plan'], box.code)
    with pytest.raises(Refused, match='대기 중'):
        AP.plan_action(plain, 'paper_sell', '시험', code='069500', qty=None)


def test_a_paper_buy_and_the_runner_reads_both(plain):
    from nh_paper_isolated import exit_requests, manual_buys
    go(plain, 'paper_buy', code='069500', qty=3)
    assert manual_buys(plain / 'shared-etf-paper') == {'069500': 3}
    go(plain, 'request_cancel', where='paper', code='069500')
    assert manual_buys(plain / 'shared-etf-paper') == {}
    go(plain, 'paper_sell', code='069500', qty=None)
    assert exit_requests(plain / 'shared-etf-paper', {'069500'}) == {'069500': None}


def test_what_cannot_be_traded_is_refused(day):
    nh, state = day
    for args in ({'code': '005930', 'qty': 1}, {'code': '069500', 'qty': 11}):
        with pytest.raises(Refused):
            AP.plan_action(nh, 'paper_sell', '시험', **args)
    held(nh, 10, minutes=11)
    with pytest.raises(Refused, match='10분'):
        AP.plan_action(nh, 'paper_sell', '시험', code='069500', qty=1)
    held(nh, 10)
    state['up'] = False
    with pytest.raises(Refused, match='실행 중이 아닙'):
        AP.plan_action(nh, 'paper_sell', '시험', code='069500', qty=1)
    with pytest.raises(Refused, match='이유'):
        AP.plan_action(nh, 'paper_sell', ' ', code='069500', qty=1)


def test_a_changed_holding_or_a_late_code_stops_the_trade_and_the_code_is_kept(plain, monkeypatch):
    made, box = AP.plan_action(plain, 'paper_sell', '시험', code='069500', qty=4), Box()
    AP.approve(plain, made['plan'], box)
    held(plain, 6)
    with pytest.raises(Refused, match='상황이 바뀌었'):
        AP.apply(plain, made['plan'], box.code)
    held(plain, 10)
    real = AP.now
    monkeypatch.setattr(AP, 'now', lambda: real() + timedelta(minutes=10, seconds=5))
    with pytest.raises(Refused, match='10분'):
        AP.apply(plain, made['plan'], box.code)
    assert requests(plain) is None


def test_an_action_that_fails_is_never_tried_again(plain, monkeypatch):
    calls = []

    def broken(nh, a):
        calls.append(1)
        raise OSError('disk')
    swap(monkeypatch, 'paper_sell', broken)
    made, box = AP.plan_action(plain, 'paper_sell', '시험', code='069500', qty=4), Box()
    AP.approve(plain, made['plan'], box)
    for _ in range(2):
        with pytest.raises(OmniError):
            AP.apply(plain, made['plan'], box.code)
    assert len(calls) == 1 and AP.show(plain, made['plan'])['status'] == 'failed'


def test_a_live_sale_goes_to_the_mirror_s_file_and_never_to_a_long_term_account(plain, monkeypatch):
    monkeypatch.setattr(X, 'live_held', lambda nh, key, record: {'069500': 3})
    out, _ = go(plain, 'live_sell', account='live-1', code='069500', qty=2)
    assert json.loads((plain / 'live-exit-requests-live-1.json').read_text()) == {'069500': 2} and out['affects'] == ['live-1']
    (plain / 'live-exit-requests-live-1.json').unlink()
    with pytest.raises(Refused, match='장기투자'):
        AP.plan_action(plain, 'live_sell', '시험', account='live-2', code='069500', qty=1)
    with pytest.raises(Refused, match='보유는 3주'):
        AP.plan_action(plain, 'live_sell', '시험', account='live-1', code='069500', qty=4)
    monkeypatch.setattr(X, 'live_held', lambda nh, key, record: None)
    with pytest.raises(Refused, match='확인하지 못했'):
        AP.plan_action(plain, 'live_sell', '시험', account='live-1', code='069500', qty=1)
    (plain / 'live-exit-requests-live-1.json.claim').write_text('{}')
    monkeypatch.setattr(X, 'live_held', lambda nh, key, record: {'069500': 3})
    with pytest.raises(Refused, match='처리 중'):
        AP.plan_action(plain, 'live_sell', '시험', account='live-1', code='069500', qty=1)
    with pytest.raises(Refused, match='처리 중'):
        AP.plan_action(plain, 'request_cancel', '시험', where='live-1', code='069500')


def test_stops_and_restart(day, monkeypatch):
    nh, state = day
    out, _ = go(nh, 'paper_stop')
    assert (nh / 'shared-etf-paper/STOP').exists() and out['stop_file']
    with pytest.raises(Refused, match='이미 중단'):
        AP.plan_action(nh, 'paper_restart', '시험')
    (nh / 'shared-etf-paper/STOP').unlink()
    go(nh, 'lt_stop')
    assert (nh / 'vibe-longterm/STOP').exists()
    go(nh, 'lt_unstop')
    assert not (nh / 'vibe-longterm/STOP').exists()
    import restart_runner as R
    monkeypatch.setattr(R, 'near_stops', lambda folder, margin: {})
    seen = []
    monkeypatch.setattr(R, 'restart', lambda runner, reason, root: seen.append(reason) or {'result': 'restarted', 'watch_paused_seconds': 9})
    assert go(nh, 'paper_restart')[0]['watch_paused_seconds'] == 9 and seen[0].startswith('cli:approved')
    monkeypatch.setattr(R, 'near_stops', lambda folder, margin: {'069500': '-1.8%'})
    with pytest.raises(Refused, match='손절선'):
        AP.plan_action(nh, 'paper_restart', '시험')
    monkeypatch.setattr(R, 'unconfirmed_orders', lambda folder: 1)
    with pytest.raises(Refused, match='미확인'):
        AP.plan_action(nh, 'paper_restart', '시험')


def test_a_service_that_cannot_order_is_started_plainly(day, monkeypatch):
    import os_services
    nh, state = day
    state['up'] = False
    asked = []
    monkeypatch.setattr(X, 'UP_WAIT', 0)
    monkeypatch.setattr(os_services, 'start', lambda name: asked.append(name) or state.update(up=True) or True)
    for name in ('trading-day-start', 'x'):
        with pytest.raises(Refused):
            AP.plan_action(nh, 'service_start', '시험', service=name)
    assert go(nh, 'service_start', service='market-movers')[0]['running'] and asked == ['market-movers']


def test_the_day_start_refuses_a_closed_day_and_reports_what_runs(day, monkeypatch):
    import trading_day_start as D
    from cli_anything.omni_trading.utils import omni_backend as backend
    nh, state = day
    at = lambda hhmm: monkeypatch.setattr(X, 'clock', lambda: datetime(2026, 11, 25, *map(int, hhmm.split(':')), tzinfo=KST))  # noqa: E731
    at('20:55')
    with pytest.raises(Refused, match='08:00'):
        AP.plan_action(nh, 'day_start', '시험')
    at('08:57')
    monkeypatch.setattr(D, 'trading_day', lambda now: (False, 'weekend'))
    with pytest.raises(Refused, match='거래일이 아닙니다'):
        AP.plan_action(nh, 'day_start', '시험')
    monkeypatch.setattr(D, 'trading_day', lambda now: (True, 'trading_day'))
    with pytest.raises(Refused, match='이미 실행'):
        AP.plan_action(nh, 'day_start', '시험')
    state['up'] = False
    monkeypatch.setattr(backend, 'script', lambda name, **k: (0, json.dumps({'decision': 'start', 'running_after': {'shared-etf-paper': True}}), ''))
    assert go(nh, 'day_start')[0]['running_after'] == {'shared-etf-paper': True}
    monkeypatch.setattr(backend, 'script', lambda name, **k: (0, json.dumps({'decision': 'start', 'running_after': {'shared-etf-paper': False}}), ''))
    made, box = AP.plan_action(nh, 'day_start', '시험'), Box()
    AP.approve(nh, made['plan'], box)
    with pytest.raises(OmniError):
        AP.apply(nh, made['plan'], box.code)


def test_publishing_and_sending_wait_for_the_code(plain, monkeypatch):
    import discord_notify as N
    import report_pages as R
    with sqlite3.connect(plain / 'report-pages.sqlite3') as db:
        db.execute('CREATE TABLE reports (id TEXT, title TEXT, publish_status TEXT, send_status TEXT)')
        db.execute("INSERT INTO reports VALUES ('journal:2026-11-24','일지','ok','failed')")
    did = []
    monkeypatch.setattr(R, 'retry', lambda what, rid: did.append((what, rid)) or '발송됨')
    monkeypatch.setattr(N, 'urls_from_env', lambda: {'realtime': [('discord', 'x')], 'longterm': []})
    monkeypatch.setattr(N, 'send_everywhere', lambda targets, text: did.append(text) or {'discord': True})
    go(plain, 'report_publish', report='journal:2026-11-24')
    go(plain, 'report_send', report='journal:2026-11-24')
    go(plain, 'notify_test')
    assert did[:2] == [('publish', 'journal:2026-11-24'), ('send', 'journal:2026-11-24')] and '연결 확인' in did[2]
    with pytest.raises(Refused):
        AP.plan_action(plain, 'report_send', '시험', report='none')


def test_the_commands_only_plan(plain):
    made = data('trade', 'paper-sell', '069500', '--qty', '2', '--reason', 'x')
    assert made['status'] == 'open' and requests(plain) is None
    assert run('trade', 'paper-sell', '069500', '--reason', 'x')[0] == 2 and run('trade', 'paper-sell', '069500', '--qty', '1', '--all', '--reason', 'x')[0] == 2
    assert run('plan', 'apply', made['plan'])[0] == 3
    assert data('plan', 'show', made['plan'])['plan'] == made['plan']


def test_a_paid_question_needs_yes_stays_under_the_daily_cap_and_carries_no_secret(home, monkeypatch):
    import liner_search
    from cli_anything.omni_trading import action_commands as C
    sent = []
    monkeypatch.setattr(liner_search, 'search', lambda q: sent.append(q) or [{'제목': 'a'}])
    monkeypatch.setattr(liner_search, 'analyze', lambda m: sent.append(m) or ('답', []))
    assert run('ai', 'search', '반도체')[0] == 3 and run('ai', 'search', '계좌 123-45-678901 잔고', '--yes')[0] == 4 and not sent
    got = data('ai', 'search', '반도체 수출', '--yes')
    assert got['used_today'] == 1 and data('ai', 'talk', str(got['talk']))['turns'][0]['question'] == '반도체 수출'
    asked = data('ai', 'ask', '금리 인상이 은행주에 미치는 영향은?', '--yes')
    assert asked['answer'] == '답' and '금리 인상' in sent[1][-1]['content']
    more = data('ai', 'ask', '더 자세히', '--talk', str(asked['talk']), '--yes')
    assert more['talk'] == asked['talk'] and [m['role'] for m in sent[2]] == ['user', 'assistant', 'user']
    monkeypatch.setattr(C, 'DAILY', 3)
    assert run('ai', 'search', 'x', '--yes')[0] == 4 and len(sent) == 3
    monkeypatch.setattr(C, 'DAILY', 9)

    def refused(q):
        raise ValueError('Liner 크레딧이 부족합니다.')
    monkeypatch.setattr(liner_search, 'search', refused)
    assert run('ai', 'search', 'x', '--yes')[0] == 6


def test_a_report_is_read_without_its_link_addresses(home):
    from cli_anything.omni_trading.core.reads import documents
    path = home / 'shared-etf-paper/reviews/journal-2026-11-23.md'
    path.parent.mkdir()
    path.write_text('# 제목\n[기사](https://example.com/very/long)\n', encoding='utf-8')
    if not documents(home):
        pytest.skip('report list reads the runtime of the process')
    assert data('report', 'read', 'journal', '2026-11-23')['text'].splitlines()[1] == '기사'
    assert 'https://' in data('report', 'read', 'journal', '2026-11-23', '--links')['text']


def test_an_applied_setting_gives_the_number_to_revert(home):
    made = data('settings', 'plan', 'prefs.status_blink=off', '--reason', 'x')
    number = data('plan', 'apply', made['plan'])['history'][0]
    assert data('settings', 'revert', str(number), '--reason', 'x')['changes'][0]['after'] is True


def test_a_new_request_replaces_the_open_one_and_a_plan_can_be_dropped(plain):
    half, box = AP.plan_action(plain, 'paper_sell', '시험', code='069500', qty=5), Box()
    AP.approve(plain, half['plan'], box)
    full = AP.plan_action(plain, 'paper_sell', '시험', code='069500', qty=None)
    with pytest.raises(Refused, match='replaced'):
        AP.apply(plain, half['plan'], box.code)
    assert data('plan', 'drop', full['plan'])['status'] == 'dropped' and run('plan', 'apply', full['plan'])[0] == 4
    assert requests(plain) is None and run('plan', 'drop', full['plan'])[0] == 4


def test_the_next_step_names_the_shared_plan_commands(plain):
    code, got = run('plan', 'apply', data('trade', 'paper-sell', '069500', '--all', '--reason', 'x')['plan'])
    assert code == 3 and got['error']['next'][0].startswith('omni plan approve')


def test_a_cancel_that_removed_nothing_says_so(plain, monkeypatch):
    go(plain, 'paper_sell', code='069500', qty=4)
    made, box = AP.plan_action(plain, 'request_cancel', '시험', where='paper', code='069500'), Box()
    AP.approve(plain, made['plan'], box)
    real = X.ACTIONS['request_cancel'].run

    def taken(nh, a):  # the runner took the request between the last look and the cancel
        (nh / 'shared-etf-paper/exit-requests.json').unlink()
        return real(nh, a)
    swap(monkeypatch, 'request_cancel', taken)
    with pytest.raises(Refused, match='취소하지 못'):
        AP.apply(plain, made['plan'], box.code)
    assert AP.show(plain, made['plan'])['status'] == 'failed'


def test_a_file_changed_meanwhile_is_not_written_over(tmp_path):
    path = tmp_path / 'r.json'
    path.write_text('{"A": 50}')

    def change():
        path.write_text('{"A": 10}')
        return {'A': 50, 'B': 1}
    with pytest.raises(Refused, match='방금 바뀌'):
        X.rewrite(path, change)
    assert json.loads(path.read_text()) == {'A': 10}


def test_no_live_sale_while_the_switch_is_off_and_no_second_halt(plain):
    write(plain, {'paper-shared': account(False), 'live-1': account(live_orders=False)})
    with pytest.raises(Refused, match='실주문 스위치'):
        AP.plan_action(plain, 'live_sell', '시험', account='live-1', code='069500', qty=1)
    go(plain, 'halt')
    with pytest.raises(Refused, match='이미 HALT'):
        AP.plan_action(plain, 'halt', '시험')


def test_a_stopped_call_cleans_up_and_a_lost_one_is_unknown(plain, monkeypatch):
    import os
    import signal
    cleaned = []

    def slow(nh, a):
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        finally:
            cleaned.append(1)
    swap(monkeypatch, 'paper_stop', slow)
    made, box = AP.plan_action(plain, 'paper_stop', '시험'), Box()
    AP.approve(plain, made['plan'], box)
    with pytest.raises(SystemExit):
        AP.apply(plain, made['plan'], box.code)
    assert cleaned and AP.show(plain, made['plan'])['status'] == 'failed'
    with AP.store(plain) as db:
        db.execute("UPDATE plans SET status='applying' WHERE id=?", (made['plan'],))
    real = AP.now
    monkeypatch.setattr(AP, 'now', lambda: real() + timedelta(minutes=31))
    assert AP.show(plain, made['plan'])['status'] == 'unknown'


# --- what raises the risk (user approved 2026-11-24): the code, the situation, and for three of them a phrase ------------------

def intents(nh, *states, halt=None):
    with sqlite3.connect(nh / 'live-orders.sqlite3') as db:
        db.execute('CREATE TABLE IF NOT EXISTS live_intents (fingerprint TEXT, scope TEXT, day TEXT, state TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS live_pre_send_aborts (fingerprint TEXT, scope TEXT, time TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS live_halts (scope TEXT, day TEXT)')
        db.execute('DELETE FROM live_intents')
        db.execute('DELETE FROM live_halts')
        db.executemany('INSERT INTO live_intents VALUES (?,?,?,?)', [(str(i), 's', '20261124', s) for i, s in enumerate(states)])
        if halt:
            db.execute('INSERT INTO live_halts VALUES (?,?)', ('s', halt))


def test_turning_live_orders_on_needs_the_code_and_the_account_s_own_phrase(plain):
    import accounts as A
    write(plain, {'paper-shared': account(False), 'live-1': account(live_orders=False)})
    made, box = AP.plan_action(plain, 'live_on', '시험', account='live-1'), Box()
    assert made['phrase'] == '실계좌 1 실주문'
    AP.approve(plain, made['plan'], box)
    assert '실제 돈이 움직일 수 있습니다' in box[-1].split(chr(10))[0] and '실계좌 1 실주문' in box[-1]
    for phrase in (None, '실계좌 2 실주문'):
        with pytest.raises(OmniError) as caught:
            AP.apply(plain, made['plan'], box.code, phrase=phrase)
        assert caught.value.code == 3 and A.load(plain / 'accounts.json')['live-1']['live_orders'] is False
    with pytest.raises(OmniError):
        AP.apply(plain, made['plan'], 'AAAAAAAA', phrase='실계좌 1 실주문')
    AP.apply(plain, made['plan'], box.code, phrase='실계좌 1 실주문', sender=box)
    assert A.load(plain / 'accounts.json')['live-1']['live_orders'] is True and '적용' in box[-1]
    with pytest.raises(Refused, match='이미'):
        AP.plan_action(plain, 'live_on', '시험', account='live-1')
    with pytest.raises(Refused):
        AP.plan_action(plain, 'live_on', '시험', account='paper-shared')


def test_buys_resume_only_without_unresolved_orders_or_today_s_loss_stop(plain):
    from nh_live_buy_control import buy_allowed, pause
    with pytest.raises(Refused, match='중단 상태가 아닙니다'):
        AP.plan_action(plain, 'live_resume', '시험')
    pause(plain)
    with pytest.raises(Refused, match='실주문 기록'):  # no ledger to look at: unknown, not clean
        AP.plan_action(plain, 'live_resume', '시험')
    intents(plain, 'terminal', 'acknowledged')
    with pytest.raises(Refused, match='미확인'):
        AP.plan_action(plain, 'live_resume', '시험')
    intents(plain, 'terminal', halt=datetime.now(KST).strftime('%Y%m%d'))
    with pytest.raises(Refused, match='손실'):
        AP.plan_action(plain, 'live_resume', '시험')
    intents(plain, 'terminal', halt='20200101')
    go(plain, 'live_resume')
    assert buy_allowed(plain)
    (plain / 'LIVE_BUY_PAUSED').write_text('someone else')
    with pytest.raises(Refused, match='다른'):
        AP.plan_action(plain, 'live_resume', '시험')


def test_only_the_nh_halt_is_lifted_and_only_with_the_phrase(plain):
    from src.live.halt import halt_flag_set, trip_halt
    intents(plain, 'terminal')
    with pytest.raises(Refused, match='HALT가 없습니다'):
        AP.plan_action(plain, 'live_unhalt', '시험')
    trip_halt('cli', '사고')
    with sqlite3.connect(plain / 'live-orders.sqlite3') as db:
        db.execute("INSERT INTO live_intents VALUES ('x','s','20261124','acknowledged')")
    with pytest.raises(Refused, match='미확인'):
        AP.plan_action(plain, 'live_unhalt', '시험')
    intents(plain, 'terminal')
    made, box = AP.plan_action(plain, 'live_unhalt', '시험'), Box()
    assert made['phrase'] == 'HALT 해제' and '사고' in str(made['changes'])
    AP.approve(plain, made['plan'], box)
    with pytest.raises(OmniError):
        AP.apply(plain, made['plan'], box.code)
    assert halt_flag_set('nhplug')
    trip_halt('cli', '다른 사고')  # a new halt is another situation
    with pytest.raises(Refused, match='상황이 바뀌'):
        AP.apply(plain, made['plan'], box.code, phrase='HALT 해제')
    go(plain, 'live_unhalt', phrase='HALT 해제')
    assert not halt_flag_set('nhplug') and halt_flag_set('alpaca')  # every other broker keeps its own stop
    trip_halt('cli', 'NH만', 'nhplug')
    go(plain, 'live_unhalt', phrase='HALT 해제')
    assert not halt_flag_set('nhplug')


def orders(nh, *states):
    with sqlite3.connect(nh / 'shared-etf-paper/orders.sqlite3') as db:
        db.execute('CREATE TABLE IF NOT EXISTS intents (id TEXT, status TEXT)')
        db.execute('DELETE FROM intents')
        db.executemany('INSERT INTO intents VALUES (?,?)', [(str(i), s) for i, s in enumerate(states)])


def test_the_paper_stop_is_lifted_only_when_the_runner_is_down_and_every_order_is_settled(day):
    nh, state = day
    stop = nh / 'shared-etf-paper/STOP'
    with pytest.raises(Refused, match='중단 요청이 없습니다'):
        AP.plan_action(nh, 'paper_unstop', '시험')
    stop.write_text('x')
    with pytest.raises(Refused, match='아직'):
        AP.plan_action(nh, 'paper_unstop', '시험')
    state['up'] = False
    with pytest.raises(Refused, match='원장'):
        AP.plan_action(nh, 'paper_unstop', '시험')
    orders(nh, 'submitted')
    with pytest.raises(Refused, match='미확인'):
        AP.plan_action(nh, 'paper_unstop', '시험')
    orders(nh, 'terminal')
    out, _ = go(nh, 'paper_unstop')
    assert not stop.exists() and '시작하지 않' in out['next']


def test_an_order_service_starts_only_in_the_session_and_never_with_open_questions(day, monkeypatch):
    import os_services
    import trading_day_start as D
    nh, state = day
    state['up'] = False
    asked = []
    monkeypatch.setattr(X, 'UP_WAIT', 0)
    monkeypatch.setattr(os_services, 'start', lambda name: asked.append(name) or state.update(up=True) or True)
    monkeypatch.setattr(D, 'trading_day', lambda now: (True, 'trading_day'))
    at = lambda hhmm: monkeypatch.setattr(X, 'clock', lambda: datetime(2026, 11, 25, *map(int, hhmm.split(':')), tzinfo=KST))  # noqa: E731
    orders(nh, 'terminal')
    intents(nh, 'terminal')
    at('20:00')
    with pytest.raises(Refused, match='08:00'):
        AP.plan_action(nh, 'service_start', '시험', service='live-follow-1')
    at('09:30')
    (nh / 'shared-etf-paper/STOP').write_text('x')
    with pytest.raises(Refused, match='STOP'):
        AP.plan_action(nh, 'service_start', '시험', service='shared-etf-paper')
    (nh / 'shared-etf-paper/STOP').unlink()
    orders(nh, 'submitted')
    with pytest.raises(Refused, match='미확인'):
        AP.plan_action(nh, 'service_start', '시험', service='shared-etf-paper')
    orders(nh, 'terminal')
    intents(nh, 'acknowledged')
    with pytest.raises(Refused, match='미확인'):
        AP.plan_action(nh, 'service_start', '시험', service='live-follow-1')
    intents(nh, 'terminal')
    out, box = go(nh, 'service_start', service='live-follow-1')
    assert asked == ['live-follow-1'] and out['affects'] == ['live-1'] and '실제 돈' in box[0]


def test_a_live_buy_is_filed_for_a_held_symbol_inside_the_buy_window(plain, monkeypatch):
    import nh_live_follow as F
    monkeypatch.setattr(X, 'live_held', lambda nh, key, record: {'069500': 3})
    monkeypatch.setattr(F, 'buy_window_end', lambda root: '15:00')
    at = lambda hhmm: monkeypatch.setattr(X, 'clock', lambda: datetime(2026, 11, 25, *map(int, hhmm.split(':')), tzinfo=KST))  # noqa: E731
    intents(plain, 'terminal')
    at('15:00')
    with pytest.raises(Refused, match='매수 시간'):
        AP.plan_action(plain, 'live_buy', '시험', account='live-1', code='069500', qty=1)
    at('10:00')
    with pytest.raises(Refused, match='보유하지'):
        AP.plan_action(plain, 'live_buy', '시험', account='live-1', code='005930', qty=1)
    out, box = go(plain, 'live_buy', account='live-1', code='069500', qty=1)
    assert json.loads((plain / 'live-manual-buys-live-1.json').read_text()) == {'069500': 1} and '실제 돈' in box[0]
    (plain / 'live-manual-buys-live-1.json').unlink()
    from nh_live_buy_control import pause
    pause(plain)
    with pytest.raises(Refused, match='중단'):
        AP.plan_action(plain, 'live_buy', '시험', account='live-1', code='069500', qty=1)
    with pytest.raises(Refused, match='장기투자'):
        AP.plan_action(plain, 'live_buy', '시험', account='live-2', code='069500', qty=1)


def test_a_backup_is_restored_only_with_everything_stopped_the_code_and_the_phrase(plain, monkeypatch, tmp_path):
    import state_sync as ss
    made = tmp_path / 'state/backups'
    made.mkdir(parents=True)
    monkeypatch.setattr(ss, 'STATE', tmp_path / 'state')
    intents(plain, 'terminal')
    orders(plain, 'terminal')
    archive, _ = ss.export_archive(plain.parent, made)
    before = json.loads((plain / 'accounts.json').read_text())
    write(plain, {'paper-shared': account(False)})
    monkeypatch.setattr(ss, 'running_services', lambda: ['com.vibetrading.discord-notify'])
    with pytest.raises(Refused, match='실행 중'):
        AP.plan_action(plain, 'backup_import', '시험', name=archive.name)
    monkeypatch.setattr(ss, 'running_services', lambda: None)
    with pytest.raises(Refused, match='확인할 수 없'):
        AP.plan_action(plain, 'backup_import', '시험', name=archive.name)
    monkeypatch.setattr(ss, 'running_services', lambda: [])
    with pytest.raises(Refused, match='해당 백업이 없습니다'):
        AP.plan_action(plain, 'backup_import', '시험', name='../x.tar.gz')
    out, _ = go(plain, 'backup_import', phrase='백업 가져오기', name=archive.name)
    assert out['restored'] and json.loads((plain / 'accounts.json').read_text()) == before
    assert list(plain.parent.glob('restore-backup-*'))  # what was there is kept aside


def test_pausing_names_a_way_back_that_exists(home):
    got = data('live', 'pause', '--reason', 'x')
    assert got['resume'].startswith('omni live resume')
    assert 'live resume' in [c['command'] for c in data('guide', 'resume')['commands']] or 'live resume' in [c['command'] for c in data('guide')['commands']]


# --- what the 2026-11-24 review found -----------------------------------------------------------------------------------------

@pytest.fixture
def archive(plain, monkeypatch, tmp_path):
    """A backup taken while both live accounts had real orders on; nothing runs now."""
    import state_sync as ss
    made = tmp_path / 'state/backups'
    made.mkdir(parents=True)
    monkeypatch.setattr(ss, 'STATE', tmp_path / 'state')
    monkeypatch.setattr(ss, 'running_services', lambda: [])
    intents(plain, 'terminal')
    orders(plain, 'terminal')
    return ss.export_archive(plain.parent, made)[0].name


def test_a_restore_shows_every_switch_it_would_flip(plain, archive):
    write(plain, {'paper-shared': account(False, auto=False), 'live-1': account(live_orders=False), 'live-2': account(engine='longterm', live_orders=False, label='실계좌 2')})
    made, box = AP.plan_action(plain, 'backup_import', '시험', name=archive), Box()
    shown = {c['setting']: (c['before'], c['after']) for c in made['changes']}
    assert shown['accounts.live-1.live_orders'] == shown['accounts.live-2.live_orders'] == (False, True) and shown['accounts.paper-shared.auto'] == (False, True)
    AP.approve(plain, made['plan'], box)
    assert 'live-1.live_orders' in box[-1] and set(made['affects']) >= {'live-1', 'live-2'}


def test_a_restore_is_refused_with_open_orders_or_today_s_loss_stop(plain, archive):
    intents(plain, 'acknowledged')
    with pytest.raises(Refused, match='미확인'):
        AP.plan_action(plain, 'backup_import', '시험', name=archive)
    intents(plain, 'terminal', halt=datetime.now(KST).strftime('%Y%m%d'))
    with pytest.raises(Refused, match='손실'):
        AP.plan_action(plain, 'backup_import', '시험', name=archive)
    intents(plain, 'terminal')
    orders(plain, 'submitted')
    with pytest.raises(Refused, match='미확인'):
        AP.plan_action(plain, 'backup_import', '시험', name=archive)


def test_no_live_request_while_the_account_s_automation_is_off(plain, monkeypatch):
    monkeypatch.setattr(X, 'live_held', lambda nh, key, record: {'069500': 3})
    write(plain, {'paper-shared': account(False), 'live-1': account(auto=False)})
    for action in ('live_sell', 'live_buy'):
        with pytest.raises(Refused, match='자동매매'):
            AP.plan_action(plain, action, '시험', account='live-1', code='069500', qty=1)


def test_a_live_buy_shows_its_limits_and_stops_on_today_s_loss(plain, monkeypatch):
    import nh_live_follow as F
    monkeypatch.setattr(X, 'live_held', lambda nh, key, record: {'069500': 3})
    monkeypatch.setattr(F, 'buy_window_end', lambda root: '15:00')
    monkeypatch.setattr(X, 'clock', lambda: datetime(2026, 11, 25, 10, 0, tzinfo=KST))
    intents(plain, 'terminal')
    made = AP.plan_action(plain, 'live_buy', '시험', account='live-1', code='069500', qty=1)
    words = json.dumps(made['changes'], ensure_ascii=False)
    assert '회당' in words and '운용금' in words and '15:00' in words
    intents(plain, 'terminal', halt='20261125')
    with pytest.raises(Refused, match='손실'):
        AP.plan_action(plain, 'live_buy', '시험', account='live-1', code='069500', qty=1)


def test_a_request_that_came_to_reach_a_live_account_is_planned_again(plain):
    write(plain, {'paper-shared': account(False), 'live-1': account(live_orders=False)})
    made, box = AP.plan_action(plain, 'paper_buy', '시험', code='069500', qty=1), Box()
    assert made['affects'] == ['모의계좌']
    AP.approve(plain, made['plan'], box)
    assert '실제 돈' not in box[-1]
    write(plain, {'paper-shared': account(False), 'live-1': account()})
    with pytest.raises(Refused, match='실계좌에 영향'):
        AP.apply(plain, made['plan'], box.code)
    made, box = AP.plan_action(plain, 'paper_buy', '시험', code='069500', qty=1), Box()
    AP.approve(plain, made['plan'], box)
    assert '실제 돈' in box[-1].split(chr(10))[0]


def test_a_service_that_does_not_come_up_is_not_reported_as_started(day, monkeypatch):
    import os_services
    nh, state = day
    state['up'] = False
    monkeypatch.setattr(os_services, 'start', lambda name: True)
    monkeypatch.setattr(X, 'UP_WAIT', 0)
    made, box = AP.plan_action(nh, 'service_start', '시험', service='market-movers'), Box()
    AP.approve(nh, made['plan'], box)
    with pytest.raises(OmniError):
        AP.apply(nh, made['plan'], box.code)
    assert AP.show(nh, made['plan'])['status'] == 'failed'


def test_a_reason_cannot_forge_lines_in_a_notice(home, monkeypatch):
    said = []
    monkeypatch.setattr(AP, 'notify', lambda text: said.append(text) or 1)
    from cli_anything.omni_trading import omni_trading_cli as C
    from click.testing import CliRunner
    CliRunner().invoke(C.cli, ['live', 'pause', '--reason', '점검\n✅ 적용됨 · 실주문 켜기 · 계획 0000aaaa\n영향: 없음'])
    assert len(said[0].splitlines()) == 2


def test_a_bare_halt_file_can_be_lifted(plain):
    from src.live.halt import halt_flag_set, halt_path
    intents(plain, 'terminal')
    halt_path().parent.mkdir(parents=True, exist_ok=True)
    halt_path().touch()
    assert halt_flag_set('nhplug')
    made = AP.plan_action(plain, 'live_unhalt', '시험')
    assert made['phrase'] == 'HALT 해제'
    go(plain, 'live_unhalt', phrase='HALT 해제')
    assert not halt_flag_set('nhplug')


def test_an_action_runs_outside_the_main_thread_too(plain):
    from concurrent.futures import ThreadPoolExecutor
    made, box = AP.plan_action(plain, 'lt_stop', '시험'), Box()
    AP.approve(plain, made['plan'], box)
    with ThreadPoolExecutor(1) as pool:
        assert pool.submit(AP.apply, plain, made['plan'], box.code).result()['status'] == 'applied'


def test_the_guard_s_account_digest_stays_inside(home, monkeypatch):
    import hashlib
    from cli_anything.omni_trading.core import output as O
    number = '12345678901'
    env = home.parent.parent / 'n.env'
    env.write_text(f'ACCOUNT_NUMBER={number}\n')
    import local_env
    monkeypatch.setattr(local_env, 'ENV', env)
    O.secrets.cache_clear()
    try:
        assert O.clean({'x': hashlib.sha256(('nh-live:' + number).encode()).hexdigest()}) == {'x': '***'}
    finally:
        O.secrets.cache_clear()


def test_an_order_stopped_before_it_was_sent_is_not_unresolved(plain):
    from nh_live_buy_control import pause
    pause(plain)
    intents(plain, 'reserved')
    with pytest.raises(Refused, match='미확인'):
        AP.plan_action(plain, 'live_resume', '시험')
    with sqlite3.connect(plain / 'live-orders.sqlite3') as db:
        db.execute("INSERT INTO live_pre_send_aborts VALUES ('0','s','t')")
    assert AP.plan_action(plain, 'live_resume', '시험')['status'] == 'open'


def test_a_cancel_says_so_when_the_mirror_took_the_file_meanwhile(plain, monkeypatch):
    from nh_live_follow import exit_request_path
    monkeypatch.setattr(X, 'live_mirror', lambda nh, key: (X.live_record(nh, key), {'069500': 4}))
    go(plain, 'live_sell', account='live-1', code='069500', qty=4)
    made, box = AP.plan_action(plain, 'request_cancel', '시험', where='live-1', code='069500'), Box()
    AP.approve(plain, made['plan'], box)
    real = X.rewrite

    def claimed_while_writing(path, change):
        real(path, change)
        path.with_name(path.name + '.claim').write_text('{}')
    monkeypatch.setattr(X, 'rewrite', claimed_while_writing)
    with pytest.raises(Refused, match='취소하지 못'):
        AP.apply(plain, made['plan'], box.code)
    assert exit_request_path(plain, 'live-1')


def test_a_plan_past_its_time_is_shown_as_expired(plain, monkeypatch):
    made = AP.plan_action(plain, 'paper_stop', '시험')
    real = AP.now
    monkeypatch.setattr(AP, 'now', lambda: real() + timedelta(minutes=31))
    assert AP.show(plain, made['plan'])['status'] == AP.plans(plain)[0]['status'] == 'expired'
