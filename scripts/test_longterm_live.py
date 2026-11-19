import fcntl
import json
import sys
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import longterm_dca as D
import longterm_live as LL
import vibe_longterm as L
from src.tools.nhplug_live_guard import LiveOrderBlocked, LiveOrderGuard
from test_vibe_longterm import ind

KST = L.KST
DAY = '2026-11-18'
PLAN = {'day': DAY, 'indicators': ind(), 'regime': 'bull', 'mode': 'manual', 'active': ['bogle_core', 'dalio_rebalance']}
QUOTES = {k: (10_000, 10_010) for k in L.ASSETS}
CAPITAL = 500_000
ACCOUNT = '00000000002'
RECORD = {'budget': {'capital_krw': CAPITAL, 'order_price_max_krw': 2_000_000}}
CODE = {k: a[0] for k, a in L.ASSETS.items()}
at = lambda h, m: datetime(2026, 11, 18, h, m, tzinfo=KST)


@pytest.fixture(autouse=True)
def krx_calendar(monkeypatch):
    """The KRX holiday table as the engine caches it, so no test asks KRX."""
    monkeypatch.setattr(L, '_HOLIDAYS', {year: frozenset() for year in range(2024, 2031)}, raising=False)


def test_day_orders_size_the_engine_plan_on_the_account_capital():
    orders, info, book = LL.day_orders(PLAN, {}, {}, {}, QUOTES, 10_000_000, CAPITAL, DAY)
    assert info['nav'] == CAPITAL  # a bigger cash balance does not widen the account's capital
    assert orders and all(o['side'] == 'BUY' and o['qty'] > 0 for o in orders)
    assert sum(o['qty'] * o['price'] for o in orders) <= CAPITAL
    assert book.get('deploying')  # the engine's first-deployment state, saved only when the orders really go out


def test_day_orders_sell_before_buying_and_never_more_than_held():
    held = {L.ASSETS['NDX'][0]: 40}  # 400,000 KRW in one ETF against a 30% target
    orders, _, _ = LL.day_orders(PLAN, {}, {'deploying': False}, held, QUOTES, 100_000, CAPITAL, DAY)
    sells = [o for o in orders if o['side'] == 'SELL']
    assert sells and orders[0]['side'] == 'SELL' and all(o['side'] == 'BUY' for o in orders[len(sells):])
    assert all(o['qty'] <= 40 for o in sells)  # 40 shares held


class Broker:
    """The broker as one cycle sees it: the balance snapshot, quotes and the guarded order path (fakes, no network)."""

    def __init__(self, cash=600_000, held=(), avg=10_000, quote=(10_000, 10_010)):
        self.cash, self.held, self.avg, self.quote = cash, dict(held), avg, quote
        self.refuse = {}  # code -> LiveOrderBlocked message
        self.sent, self.reads, self.replacements = [], 0, []

    def snapshots(self, reader, account, now):
        self.reads += 1
        rows = [{'iem_cd': c, 'rsdl_qty': q, 'phs_pr': self.avg, 'now_pr': self.quote[0]} for c, q in self.held.items()]
        cost = sum(q * self.avg for q in self.held.values())  # what the guard's capital check counts (tot_byn_amt)
        return {'history': {}, 'balance': {'Output_0': {'orr_pbl_amt1': self.cash, 'tot_byn_amt': cost}, 'Output_1': rows}}

    def bid_ask(self, reader, code):
        self.reads += 1
        return self.quote

    def execute(self, reader, guard, credentials, op, fields, replacement_of=None):
        self.replacements.append(replacement_of)
        if fields['iem_cd'] not in guard.symbols:  # as LiveOrderGuard.reserve: a guard orders only the symbols it was built with
            raise LiveOrderBlocked('account or symbol mismatch')
        if fields['iem_cd'] in self.refuse:
            raise LiveOrderBlocked(self.refuse[fields['iem_cd']])
        self.sent.append(('SELL' if op == 'krstockOrderCashSell' else 'BUY', fields['iem_cd'], fields['orr_qty'], fields['orr_pr']))
        return 'fp'


def run(root, now, broker, *, execute_orders, dca=None, record=RECORD, plan=PLAN, stories=None, books=None, auto=True, pending=None):
    settings = {'dca': {'live-2': D.clean(dca)} if dca else {}, 'books': books or {}}
    state = {'plan': plan, 'story': {'stories': stories or {}}}
    with patch.object(L, 'read_state', side_effect=lambda root, keys: {k: state[k] for k in keys}), \
         patch.object(L, 'load_settings', return_value=settings), \
         patch('nh_live_broker.collect_snapshots', side_effect=broker.snapshots), patch('live_fills.record'), \
         patch('nh_live_follow.pending_step', return_value=pending), patch('nh_live_follow.bid_ask', side_effect=broker.bid_ask), \
         patch('nh_guarded_live.execute', side_effect=broker.execute):
        return LL.cycle(None, ACCOUNT, {}, record, 'live-2', now=now, execute_orders=execute_orders,
                        may_cancel=execute_orders, root=root, engine_root=root, auto=auto)


def guard(root):
    return LiveOrderGuard(ACCOUNT, root, symbols=set(L.CODE_KEY))


def own(root, key, qty):
    """The operator-approved adoption of broker-held shares into the guard ledger (nh_guarded_live --adopt-existing)."""
    code = CODE[key]
    balance = {'_pagination_complete': True, '_account': ACCOUNT, 'rsp_cd': '00000', 'rsp_msg': '조회가 완료되었습니다',
               'Output_1': [{'iem_cd': code, 'rsdl_qty': qty}]}
    guard(root).adopt_existing(now=at(9, 0), balance=balance, quantity=qty, symbol=code,
                               sellable={'_account': ACCOUNT, '_symbol': code, 'rsp_cd': '00000', 'sll_pbl_qty': qty})


def unsent(root):
    return [o for o in LL.load_state(root, 'live-2')['orders'] if o['status'] in ('planned', 'dry_run')]


def send_all(root, broker, start=(9, 42), **kw):
    """Cycles a minute apart until the day has nothing left to send; the results in order."""
    results, (h, m) = [], start
    while not results or results[-1]['action'] not in ('HOLD', 'DCA', 'PLAN'):
        results.append(run(root, at(h, m), broker, execute_orders=True, **kw))
        h, m = (h + 1, 0) if m == 59 else (h, m + 1)
        assert len(results) < 30
    return results


def test_dry_run_records_the_day_plan_once_and_sends_nothing(tmp_path):
    broker = Broker()
    assert run(tmp_path, at(9, 30), broker, execute_orders=False)['action'] == 'WAIT'
    first = run(tmp_path, at(9, 41), broker, execute_orders=False)
    assert (first['action'], first['execution']) == ('PLAN', 'DRY_RUN') and first['orders']
    assert 'mode' not in first and first['engine_mode'] == 'manual'  # never shadows the log's execute/dry_run mode
    assert run(tmp_path, at(9, 42), broker, execute_orders=False)['action'] == 'HOLD'
    assert broker.sent == []
    state = LL.load_state(tmp_path, 'live-2')
    assert state['book'] == {} and {o['status'] for o in state['orders']} == {'dry_run'}


def test_turning_live_orders_on_after_a_dry_plan_plans_again_and_sends(tmp_path):
    broker = Broker()
    run(tmp_path, at(9, 41), broker, execute_orders=False)
    again = run(tmp_path, at(9, 45), broker, execute_orders=True)
    assert (again['action'], again['execution']) == ('PLAN', 'PLANNED')
    assert run(tmp_path, at(9, 46), broker, execute_orders=True)['execution'] == 'ACKNOWLEDGED_NOT_FILLED'
    assert len(broker.sent) == 1


def test_a_lasting_refusal_moves_on_a_passing_one_is_retried_and_the_book_waits_for_a_send(tmp_path):
    broker = Broker()
    plan = run(tmp_path, at(9, 41), broker, execute_orders=True)
    first, second, third = (o['code'] for o in plan['orders'][:3])
    assert LL.load_state(tmp_path, 'live-2')['book'] == {}  # nothing sent yet: the engine's book has not moved
    broker.refuse = {first: 'new live buys are paused'}  # the dashboard pause clears on its own
    paused = run(tmp_path, at(9, 42), broker, execute_orders=True)
    assert paused['execution'] == 'RETRY' and LL.load_state(tmp_path, 'live-2')['book'] == {}
    broker.refuse = {second: 'daily new symbol limit'}  # an account limit does not clear today
    assert run(tmp_path, at(9, 43), broker, execute_orders=True)['execution'] == 'ACKNOWLEDGED_NOT_FILLED'
    assert run(tmp_path, at(9, 44), broker, execute_orders=True)['execution'] == 'BLOCKED'
    assert run(tmp_path, at(9, 45), broker, execute_orders=True)['execution'] == 'ACKNOWLEDGED_NOT_FILLED'
    assert [code for _, code, _, _ in broker.sent] == [first, third]
    state = LL.load_state(tmp_path, 'live-2')
    assert [o['status'] for o in state['orders'][:3]] == ['sent', 'blocked', 'sent'] and state['book'].get('deploying')
    assert run(tmp_path, at(14, 35), broker, execute_orders=True)['action'] == 'HOLD'


def test_a_refusal_that_outlasts_the_window_leaves_the_book_for_tomorrow(tmp_path):
    broker = Broker()
    plan = run(tmp_path, at(9, 41), broker, execute_orders=True)
    broker.refuse = {o['code']: 'live kill switch active' for o in plan['orders']}  # HALT all day
    for m in range(42, 50):
        assert run(tmp_path, at(9, m), broker, execute_orders=True)['execution'] == 'RETRY'
    assert run(tmp_path, at(14, 31), broker, execute_orders=True)['action'] == 'HOLD'
    state = LL.load_state(tmp_path, 'live-2')
    assert broker.sent == [] and state['book'] == {} and {o['status'] for o in state['orders']} == {'planned'}


def test_sells_never_exceed_what_the_guard_ledger_owns(tmp_path):
    broker = Broker(cash=100_000, held=[(CODE['NDX'], 40)])  # 40 broker-held shares; only 15 were adopted
    own(tmp_path, 'NDX', 15)
    plan = run(tmp_path, at(9, 41), broker, execute_orders=True)
    assert plan['orders'][0]['side'] == 'SELL' and plan['orders'][0]['qty'] > 15
    run(tmp_path, at(9, 42), broker, execute_orders=True)
    assert broker.sent[0][:3] == ('SELL', CODE['NDX'], 15)


def test_a_sell_of_shares_the_guard_never_bought_is_skipped_with_the_reason(tmp_path):
    broker = Broker(cash=100_000, held=[(CODE['NDX'], 40)])
    run(tmp_path, at(9, 41), broker, execute_orders=True)
    result = run(tmp_path, at(9, 42), broker, execute_orders=True)
    assert result['action'] == 'SKIP' and 'adopt' in result['reason'] and broker.sent == []


@pytest.mark.parametrize('quote, limits', [((10_000, 10_010), {'order_max_krw': 30_000}), ((10, 11), {})])
def test_an_oversized_buy_is_cut_to_the_account_limits_instead_of_refused(tmp_path, quote, limits):
    from src.tools.nhplug_live_guard import ORDER_QTY_MAX
    record = {**RECORD, 'limits': limits}
    broker = Broker(quote=quote)
    planned = run(tmp_path, at(9, 41), broker, execute_orders=True, record=record)['orders']
    send_all(tmp_path, broker, record=record)
    order_max = limits.get('order_max_krw', 1_000_000)
    want = [min(o['qty'], ORDER_QTY_MAX, order_max // quote[1]) for o in planned]
    assert [qty for _, _, qty, _ in broker.sent] == want and max(want) < max(o['qty'] for o in planned)


def test_a_buy_above_the_account_share_price_cap_is_not_sent(tmp_path):
    record = {'budget': {**RECORD['budget'], 'order_price_max_krw': 10_000}}  # the ask is 10,010
    broker = Broker()
    run(tmp_path, at(9, 41), broker, execute_orders=True, record=record)
    result = run(tmp_path, at(9, 42), broker, execute_orders=True, record=record)
    assert result['action'] == 'SKIP' and 'price cap' in result['reason'] and broker.sent == []


def test_a_planned_buy_leaves_room_for_the_fee():
    assert LL.to_orders({'NDX': 30_031}, {}, QUOTES)[0]['qty'] == 2  # 3 x 10,010 = 30,030 but the 0.015% fee is on top


def test_the_buy_sent_leaves_room_for_the_fee(tmp_path):
    order = {'key': 'NDX', 'code': CODE['NDX'], 'side': 'BUY', 'qty': 3, 'price': 10_000, 'krw': 30_031,
             'status': 'planned', 'source': 'plan'}  # planned at a lower ask; 10,010 now
    LL.save_state(tmp_path, 'live-2', {'day': DAY, 'execute': True, 'orders': [order], 'info': {}, 'book': {}, 'dca': {}})
    broker = Broker()
    run(tmp_path, at(9, 50), broker, execute_orders=True)
    assert broker.sent == [('BUY', CODE['NDX'], 2, 10_010)]


def test_the_months_sell_room_counts_the_sells_sent_not_planned(tmp_path):
    plan = {**PLAN, 'active': ['bogle_core', 'dalio_rebalance', 'buffett_lowturn']}
    broker = Broker(cash=100_000, held=[(CODE['NDX'], 40)])
    own(tmp_path, 'NDX', 2)  # the guard can sell 2 of the 5 shares the engine plans to sell
    sell = run(tmp_path, at(9, 41), broker, execute_orders=True, plan=plan)['orders'][0]
    assert (sell['side'], sell['qty']) == ('SELL', 5)
    run(tmp_path, at(9, 42), broker, execute_orders=True, plan=plan)
    assert broker.sent[0][:3] == ('SELL', CODE['NDX'], 2)
    assert LL.load_state(tmp_path, 'live-2')['book']['sold'] == {'month': '2026-11', 'krw': 20_000}


def test_the_months_sell_room_keeps_only_what_filled_from_the_next_day(tmp_path):
    # user 2026-11-18: the live account counts the month's sells by fills, as the virtual book does (a sell cancelled
    # unfilled after 60 s no longer uses the Buffett monthly room)
    plan = {**PLAN, 'active': ['bogle_core', 'dalio_rebalance', 'buffett_lowturn']}
    broker = Broker(cash=100_000, held=[(CODE['NDX'], 40)])
    own(tmp_path, 'NDX', 2)
    run(tmp_path, at(9, 41), broker, execute_orders=True, plan=plan)
    run(tmp_path, at(9, 42), broker, execute_orders=True, plan=plan)  # 2 shares sent at 10,000
    with patch.object(LL, 'filled_on', side_effect=lambda root, key, day, side: {CODE['NDX']: 1} if side == 'SELL' else {}):
        run(tmp_path, datetime(2026, 11, 19, 9, 41, tzinfo=KST), broker, execute_orders=False, plan={**plan, 'day': '2026-11-19'})
    assert LL.load_state(tmp_path, 'live-2')['book']['sold'] == {'month': '2026-11', 'krw': 10_000}  # 1 of the 2 filled


def test_dca_spends_only_money_the_engine_has_not_claimed(tmp_path):
    broker = Broker()
    engine = run(tmp_path, at(9, 41), broker, execute_orders=True)
    engine_buys = sum(o['qty'] * o['price'] for o in engine['orders'] if o['side'] == 'BUY')
    dca = run(tmp_path, at(10, 0), broker, execute_orders=True, dca={'on': True, 'amount_krw': 450_000, 'day': 1})
    assert dca['action'] == 'SKIP' and any('여유 자금' in n for n in dca['notes'])  # 500,000 capital - engine buys < 450,000
    assert sum(o['qty'] * o['price'] for o in unsent(tmp_path) if o['side'] == 'BUY') == engine_buys


def test_dca_joins_the_engines_unsent_order_for_the_same_etf(tmp_path):
    broker = Broker()
    run(tmp_path, at(9, 41), broker, execute_orders=True)
    run(tmp_path, at(10, 0), broker, execute_orders=True, dca={'on': True, 'amount_krw': 300_000, 'day': 1})
    codes = [o['code'] for o in unsent(tmp_path)]
    assert len(codes) == len(set(codes))  # one order per ETF: the guard refuses an identical second order as a duplicate
    assert sum(o['qty'] * o['price'] for o in unsent(tmp_path) if o['side'] == 'BUY') <= CAPITAL


def test_the_dca_never_sells_even_with_the_old_take_profit_setting(tmp_path):
    # F1 (docs/longterm-master-dca-ideas-plan.md 1-2, user approval 2026-11-18): selling is the engine's alone
    broker = Broker(held=[(CODE['QUANT'], 4)], avg=5_000)  # QUANT at its 8% target, +100% over its cost
    own(tmp_path, 'QUANT', 4)
    plan = {**PLAN, 'indicators': ind(gap=0.18)}  # Nasdaq-100 18% over its 200-day line: overheated
    cfg = {'on': True, 'take_profit': True, 'tp_pct': 30, 'day': 28}  # the monthly contribution is not due yet
    run(tmp_path, at(9, 41), broker, execute_orders=True, plan=plan)
    run(tmp_path, at(10, 0), broker, execute_orders=True, plan=plan, dca=cfg)
    run(tmp_path, at(10, 1), broker, execute_orders=True, plan=plan, dca=cfg)
    assert broker.sent and all(side == 'BUY' for side, *_ in broker.sent)


def test_dca_contribution_is_recorded_only_once_its_order_is_sent(tmp_path):
    cfg = {'on': True, 'amount_krw': 50_000, 'day': 1}
    broker = Broker()
    run(tmp_path, at(9, 41), broker, execute_orders=True)
    broker.refuse = {c: 'capital limit exceeded' for c in CODE.values()}  # every order refused for good
    assert run(tmp_path, at(10, 0), broker, execute_orders=True, dca=cfg)['action'] == 'DCA'
    send_all(tmp_path, broker, start=(10, 1), dca=cfg)
    state = LL.load_state(tmp_path, 'live-2')
    assert broker.sent == [] and not state['dca'].get('last_contribution') and state['book'] == {}
    assert D.due(D.clean(cfg), state['dca'], datetime(2026, 11, 19, 10, 0, tzinfo=KST))  # tried again tomorrow

    other = tmp_path / 'sent'
    broker = Broker()
    run(other, at(9, 41), broker, execute_orders=True)
    run(other, at(10, 0), broker, execute_orders=True, dca=cfg)
    assert not LL.load_state(other, 'live-2')['dca'].get('last_contribution')
    send_all(other, broker, start=(10, 1), dca=cfg)
    assert broker.sent and LL.load_state(other, 'live-2')['dca']['last_contribution'] == DAY


def test_dry_run_dca_records_dry_orders_and_never_advances(tmp_path):
    broker = Broker()
    cfg = {'on': True, 'amount_krw': 100_000, 'day': 1}
    run(tmp_path, at(9, 41), broker, execute_orders=False)
    dca = run(tmp_path, at(10, 0), broker, execute_orders=False, dca=cfg)
    assert (dca['action'], dca['execution']) == ('DCA', 'DRY_RUN')
    assert run(tmp_path, at(10, 1), broker, execute_orders=False, dca=cfg)['action'] == 'HOLD'
    state = LL.load_state(tmp_path, 'live-2')
    assert broker.sent == [] and state['dca'] == {} and {o['status'] for o in state['orders']} == {'dry_run'}


def test_live_dca_buys_the_engines_target_of_the_day(tmp_path):
    plan = {**PLAN, 'active': ['bogle_core', 'lynch_story'], 'weights': None}
    stories = {'SEMI': {'status': 'broken', 'tilt': 0}}  # the engine's target for the semiconductor ETF is 0 today
    broker = Broker()
    run(tmp_path, at(9, 41), broker, execute_orders=True, plan=plan, stories=stories)
    run(tmp_path, at(10, 0), broker, execute_orders=True, plan=plan, stories=stories, dca={'on': True, 'amount_krw': 200_000, 'day': 1})
    assert unsent(tmp_path) and CODE['SEMI'] not in {o['code'] for o in unsent(tmp_path)}


def test_a_dca_short_of_money_is_skipped_for_the_day_without_polling_the_broker(tmp_path):
    held = {CODE[k]: int(a[3] * CAPITAL / 10_000) for k, a in L.ASSETS.items()}
    broker = Broker(cash=5_000, held=held.items())  # the account sits on its capital at the targets
    for k in L.ASSETS:
        own(tmp_path, k, held[CODE[k]])
    run(tmp_path, at(9, 41), broker, execute_orders=True)
    cfg = {'on': True, 'amount_krw': 100_000, 'day': 1}
    short = run(tmp_path, at(10, 0), broker, execute_orders=True, dca=cfg)
    assert (short['action'], short['reason']) == ('SKIP', 'dca: not enough free money') and short['notes']
    reads = broker.reads
    assert {run(tmp_path, at(10, m), broker, execute_orders=True, dca=cfg)['action'] for m in range(1, 10)} == {'HOLD'}
    assert broker.reads == reads  # the rest of the day reads nothing from the broker
    assert D.due(D.clean(cfg), LL.load_state(tmp_path, 'live-2')['dca'], datetime(2026, 11, 19, 10, 0, tzinfo=KST))  # still owed


def test_a_contribution_too_small_for_one_share_is_carried_to_the_next_one(tmp_path):
    # user approval 2026-11-18 (4-B②): a part under one share is not dropped; it waits for the next contribution
    broker = Broker(cash=10_000_000)
    run(tmp_path, at(9, 41), broker, execute_orders=True)
    send_all(tmp_path, broker)
    sent = len(broker.sent)
    cfg = {'on': True, 'amount_krw': 25_000, 'day': 1}  # under one share of every ETF once split by the targets (no KOFR since v2)
    tiny = run(tmp_path, at(10, 0), broker, execute_orders=True, dca=cfg)
    assert tiny['action'] == 'DCA' and any('이월' in n for n in tiny['notes'])
    dca = LL.load_state(tmp_path, 'live-2')['dca']
    assert len(broker.sent) == sent and dca['last_contribution'] == DAY and dca['contributed_krw'] == 25_000
    assert sum(dca['carry'].values()) == pytest.approx(25_000) and D.reserved(dca) == pytest.approx(25_000)
    assert run(tmp_path, at(10, 1), broker, execute_orders=True, dca=cfg)['action'] == 'HOLD'
    december = lambda h, m: datetime(2026, 12, 18, h, m, tzinfo=KST)
    plan = {**PLAN, 'day': '2026-12-18'}
    run(tmp_path, december(9, 41), broker, execute_orders=True, plan=plan)
    result = run(tmp_path, december(10, 0), broker, execute_orders=True, plan=plan, dca=cfg)
    from_dca = [o for o in unsent(tmp_path) if 'dca' in o['source'].split('+')]
    assert result['action'] == 'DCA' and from_dca  # 25,000 carried + 25,000: the largest targets reach one share


def test_live_capital_room_counts_the_watchlist_sleeve(tmp_path):
    broker = Broker(cash=600_000, held=[('005930', 30)])  # 300,000 of the 500,000 capital already in the sleeve
    plan = run(tmp_path, at(9, 41), broker, execute_orders=True)
    assert plan['action'] == 'PLAN' and plan['nav'] <= CAPITAL - 300_000
    assert sum(o['qty'] * o['price'] for o in plan['orders'] if o['side'] == 'BUY') <= CAPITAL - 300_000
    assert all(o['code'] != '005930' for o in plan['orders'])  # the engine never trades the sleeve
    dca = run(tmp_path, at(10, 0), broker, execute_orders=True, dca={'on': True, 'amount_krw': 450_000, 'day': 1})
    assert dca['action'] == 'SKIP'  # the sleeve and the engine's buys leave less than 450,000 under the capital


def test_an_account_below_its_cost_plans_on_the_capital_the_guard_counts():
    # review 2026-11-18: the plan valued holdings at the bid, the guard counts what they cost (tot_byn_amt + order <= capital):
    # bought for 480,000 and worth 400,000, a 500,000 capital leaves 20,000 to buy, not 100,000
    rows = [{'iem_cd': CODE['NDX'], 'rsdl_qty': 48, 'now_pr': 8_333.34}]
    assert LL.guard_capital(500_000, {'tot_byn_amt': 480_000}, rows) == pytest.approx(420_000, abs=1)
    assert LL.guard_capital(500_000, {'tot_byn_amt': 300_000}, rows) == 500_000  # in profit: the capital as set
    assert LL.guard_capital(500_000, {}, rows) == 500_000  # no cost total: nothing to correct
    assert LL.guard_capital(500_000, {'tot_byn_amt': 480_000}, [{'iem_cd': 'x', 'rsdl_qty': 48}]) == 500_000  # no price: no guess


def test_a_live_dca_buys_a_watchlist_code_through_the_guard(tmp_path):
    import longterm_watchlist as W
    W.save(tmp_path, {'005930': '삼성전자'})
    broker = Broker(cash=10_000_000)
    run(tmp_path, at(9, 41), broker, execute_orders=True)
    send_all(tmp_path, broker)
    cfg = {'on': True, 'amount_krw': 200_000, 'day': 1, 'pick_on': True, 'picks': ['NDX', '005930']}
    run(tmp_path, at(10, 0), broker, execute_orders=True, dca=cfg)
    assert {o['code'] for o in unsent(tmp_path)} == {CODE['NDX'], '005930'}
    send_all(tmp_path, broker, start=(10, 1), dca=cfg)
    assert ('BUY', '005930', 9, 10_010) in broker.sent  # 100,000 at the 10,010 ask, the fee on top


def test_a_finished_day_reads_nothing_from_the_broker_until_an_order_is_open(tmp_path):
    broker = Broker()
    run(tmp_path, at(9, 41), broker, execute_orders=True)
    send_all(tmp_path, broker)
    reads = broker.reads
    assert run(tmp_path, at(11, 0), broker, execute_orders=True)['reason'] == 'today done'
    assert broker.reads == reads
    history = {'_pagination_complete': True, '_account': ACCOUNT, '_day': '20261118', 'rsp_cd': '00000',
               'rsp_msg': '조회가 완료되었습니다', 'Output_0': [{'itg_orr_no': 7, 'iem_cd': CODE['NDX'], 'orr_qty': 1, 'tot_cns_qty': 0,
                                                       'ny_cns_qty': 1, 'can_qty': 0, 'sby_dit_cd_nm': '현금매수', 'orr_rjt_rsn_cd_nm': '정상'}]}
    guard(tmp_path).adopt_order(now=at(11, 0), history=history, order_number=7)  # an open order must still be reconciled
    run(tmp_path, at(11, 1), broker, execute_orders=True)
    assert broker.reads > reads


def start_main(tmp_path, monkeypatch, *argv, live_orders=False):
    """main() with its broker, .env and accounts.json replaced by fakes; returns the cycle calls."""
    import accounts
    record = {'kind': 'live', 'engine': 'longterm', 'auto': True, 'live_orders': live_orders, 'hash': accounts.digest(ACCOUNT),
              'budget': RECORD['budget']}
    linked = {'status': 'ok', 'data': {'Output_0': [{'acct_no': ACCOUNT, 'acct_type': '01'}]}}
    calls = []
    monkeypatch.setattr(sys, 'argv', ['longterm_live.py', '--account', 'live-2', '--once', *argv])
    monkeypatch.setattr(accounts, 'load', lambda *a, **k: {'live-2': record})
    monkeypatch.setattr('src.config.paths.get_runtime_root', lambda: tmp_path)
    monkeypatch.setattr('src.config.loader.load_agent_config', lambda: SimpleNamespace(mcp_servers={'nhplug_readonly': None}))
    monkeypatch.setattr('src.tools.mcp.MCPServerAdapter', lambda *a: SimpleNamespace(call_tool=lambda *a: linked))
    monkeypatch.setattr('dotenv.dotenv_values', lambda *a, **k: {'ACCOUNT_NUMBER': '00000000001', 'ACCOUNT_NUMBER_2': ACCOUNT,
                                                                 'APP_KEY': 'k', 'APP_SECRET': 's'})
    monkeypatch.setattr(LL.os, 'umask', lambda mask: 0o022)
    monkeypatch.setattr(LL, 'cycle', lambda *a, **k: calls.append(k) or {'action': 'HOLD', 'reason': 'test'})
    LL.main()
    return calls


def test_a_dry_and_an_armed_loop_for_one_account_never_run_together(tmp_path, monkeypatch):
    (tmp_path / 'nhplug').mkdir()
    with (tmp_path / 'nhplug' / 'live-follow-exec-live-2.lock').open('a') as lock:  # an armed loop for live-2 runs
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            start_main(tmp_path, monkeypatch)  # a dry loop shares state-live-2.json: it must not start
    assert start_main(tmp_path, monkeypatch)[0]['execute_orders'] is False


@pytest.mark.parametrize('argv, live_orders, mode', [((), True, 'dry_run'), (('--execute',), False, 'dry_run'),
                                                      (('--execute',), True, 'execute')])
def test_the_log_mode_is_the_mode_the_cycle_ran_in(tmp_path, monkeypatch, argv, live_orders, mode):
    calls = start_main(tmp_path, monkeypatch, *argv, live_orders=live_orders)
    event = json.loads((tmp_path / 'nhplug' / LL.LOG).read_text().splitlines()[-1])
    assert event['mode'] == mode and calls[0]['execute_orders'] is (mode == 'execute')


def cancelled(root, key, fingerprint='fp-cancelled'):
    """A sell of `key` the broker verified as cancelled (what pending_step leaves after 60 s unfilled)."""
    g = guard(root)
    with g._db() as db:
        db.execute("INSERT INTO live_intents (fingerprint, scope, day, operation, symbol, qty, state) VALUES (?,?,?,?,?,?,?)",
                   (fingerprint, g.scope, DAY, 'krstockOrderCashSell', CODE[key], 1, 'terminal'))
        db.execute('INSERT INTO sell_cancellations VALUES (?)', (fingerprint,))
    return fingerprint


def test_a_sell_after_a_verified_cancel_names_it_as_the_replacement(tmp_path):
    # the guard refuses a new sell of a symbol whose last sell was cancelled unless it names that sell (nh_live_follow does)
    from nh_live_follow import cancelled_sell
    assert cancelled_sell(guard(tmp_path), CODE['NDX']) is None
    plan = {**PLAN, 'active': ['bogle_core', 'dalio_rebalance', 'buffett_lowturn']}
    broker = Broker(cash=100_000, held=[(CODE['NDX'], 40)])
    own(tmp_path, 'NDX', 40)
    fingerprint = cancelled(tmp_path, 'NDX')
    assert cancelled_sell(guard(tmp_path), CODE['NDX']) == fingerprint
    run(tmp_path, at(9, 41), broker, execute_orders=True, plan=plan)
    run(tmp_path, at(9, 42), broker, execute_orders=True, plan=plan)
    assert broker.sent[0][:2] == ('SELL', CODE['NDX']) and broker.replacements[0] == fingerprint


def test_a_live_dca_buys_only_the_chosen_etfs(tmp_path):
    # user 2026-11-18: the 적립식 limits apply on the live account through the same longterm_dca.plan
    broker = Broker()
    run(tmp_path, at(9, 41), broker, execute_orders=True)
    run(tmp_path, at(10, 0), broker, execute_orders=True,
        dca={'on': True, 'amount_krw': 100_000, 'day': 1, 'pick_on': True, 'picks': ['SPX']})
    from_dca = {o['key'] for o in unsent(tmp_path) if 'dca' in o['source'].split('+')}
    assert from_dca == {'SPX'}


def test_a_live_master_book_buys_a_small_part_of_its_pool_at_most_once_a_week(tmp_path):
    # v3 (user approval 2026-11-18): the contribution fills the pool, the master spends the 적립금 pace x 0.7 x its multiplier
    cfg = {'on': True, 'profile': 'dalio', 'when_by': 'master', 'size_by': 'master', 'buy_every': 'week', 'amount_krw': 200_000, 'day': 1}
    bear = {**PLAN, 'regime': 'bear'}
    broker = Broker(cash=10_000_000)
    run(tmp_path, at(9, 41), broker, execute_orders=True, plan=bear)
    send_all(tmp_path, broker, plan=bear)
    first = run(tmp_path, at(10, 0), broker, execute_orders=True, plan=bear, dca=cfg)
    assert first['action'] == 'DCA' and any('× 1배' in n for n in first['notes'])
    assert [o for o in unsent(tmp_path) if 'dca' in o['source'].split('+')]
    send_all(tmp_path, broker, start=(10, 1), plan=bear, dca=cfg)
    dca = LL.load_state(tmp_path, 'live-2')['dca']
    base = 200_000 * 0.7 / 4.35
    assert dca['last_buy'] == DAY and dca['contributed_krw'] == 200_000 and D.reserved(dca) >= 200_000 - base - 1  # the rest waits
    thursday = lambda h, m: datetime(2026, 11, 19, h, m, tzinfo=KST)
    plan = {**bear, 'day': '2026-11-19'}
    run(tmp_path, thursday(9, 41), broker, execute_orders=True, plan=plan)
    second = run(tmp_path, thursday(10, 0), broker, execute_orders=True, plan=plan, dca=cfg)
    assert not [o for o in unsent(tmp_path) if 'dca' in o['source'].split('+')]
    assert any('오늘은 사지 않음' in n for n in second['notes'])
    assert LL.load_state(tmp_path, 'live-2')['dca']['last_buy'] == DAY


def test_the_days_plan_uses_and_records_the_accounts_own_strategy(tmp_path):
    # 계좌별 전략 (user 2026-11-18): 여운봉 저수지만 on the live account; the engine's regime and stories stay as they are
    books = {'live-2': L.clean_book({'strategy': 'custom', 'modules': {'yeo_reservoir': True}})}
    first = run(tmp_path, at(9, 41), Broker(), execute_orders=False, books=books)
    assert first['action'] == 'PLAN' and first['modules'] == ['yeo_reservoir']
    assert first['strategy'] == '이 계좌 전략: 저수지 분할(일반)만 (기준 비중 없음)'
    plain = run(tmp_path / 'engine', at(9, 41), Broker(), execute_orders=False)
    assert plain['modules'] == PLAN['active'] and plain['strategy'] == '이 계좌 전략: 엔진(가상 계좌)과 같음'


def test_a_quote_that_fails_for_a_moment_is_asked_again_before_the_plan_waits(tmp_path, monkeypatch):
    # 2026-11-18 09:40: one refused quote (NH 429 under load) made the whole plan wait a cycle
    monkeypatch.setattr(LL, 'QUOTE_RETRY_WAIT', (0, 0))
    broker = Broker()
    real, misses = broker.bid_ask, {'n': 0}

    def flaky(reader, code):
        if code == CODE['SPACE'] and misses['n'] < 2:
            misses['n'] += 1
            return None
        return real(reader, code)
    broker.bid_ask = flaky
    assert run(tmp_path, at(9, 41), broker, execute_orders=False)['action'] == 'PLAN'
    misses['n'] = -10  # down for good: the cycle waits as before
    broker.bid_ask = lambda reader, code: None
    assert run(tmp_path / 'b', at(9, 41), broker, execute_orders=False)['action'] == 'WAIT'


def test_a_live_dca_in_shares_buys_n_shares_of_each_chosen_etf(tmp_path):
    # 종목별 수량 기준 (user 2026-11-18): the chosen ETFs, one share each, on the set day
    broker = Broker(cash=600_000)
    run(tmp_path, at(9, 41), broker, execute_orders=True)
    send_all(tmp_path, broker)
    broker.sent.clear()
    dca = {'on': True, 'day': 1, 'size': 'shares', 'shares_n': 1, 'pick_on': True, 'picks': ['NDX', 'SPX']}
    result = run(tmp_path, at(10, 0), broker, execute_orders=True, dca=dca)
    assert result['action'] == 'DCA'
    mine = {o['code']: o['qty'] for o in result['orders'] if 'dca' in o['source']}
    assert mine == {CODE['NDX']: 1, CODE['SPX']: 1}


def test_a_live_dca_buys_the_chosen_etfs_when_the_account_strategy_holds_only_the_reservoir(tmp_path):
    # 재현 2026-11-18: 실계좌 2 '여운봉 저수지만' (코어 목표 0%) with 적립 NDX·SPX 1주씩 bought nothing, forever
    books = {'live-2': L.clean_book({'strategy': 'custom', 'modules': {'yeo_reservoir': True}})}
    broker = Broker(cash=600_000)
    run(tmp_path, at(9, 41), broker, execute_orders=True, books=books)
    send_all(tmp_path, broker, books=books)
    assert LL.load_state(tmp_path, 'live-2')['info']['want']['NDX'] == 0
    dca = {'on': True, 'day': 1, 'size': 'shares', 'shares_n': 1, 'pick_on': True, 'picks': ['NDX', 'SPX']}
    result = run(tmp_path, at(10, 0), broker, execute_orders=True, dca=dca, books=books)
    mine = {o['code']: o['qty'] for o in result['orders'] if 'dca' in o['source']}
    assert result['action'] == 'DCA' and mine == {CODE['NDX']: 1, CODE['SPX']: 1}


def test_the_yeo_method_account_waits_before_the_first_step_and_buys_seventy_thirty_at_it(tmp_path):
    # 여운봉 저수지 매매법 on 실계좌 2 (user 2026-11-18): 500,000 KRW, nothing held
    books = {'live-2': L.clean_book({'strategy': 'yeo_method'})}
    calm = {**PLAN, 'indicators': {**PLAN['indicators'], 'ath': {'ndx': {'dd': -0.15}}}}
    first = run(tmp_path, at(9, 41), Broker(cash=600_000), execute_orders=False, books=books, plan=calm)
    assert first['action'] == 'PLAN' and first['orders'] == [] and first['modules'] == []
    assert any('다음 단계' in n for n in first['notes']) and '여운봉 저수지 매매법' in first['strategy']
    fall = {**PLAN, 'indicators': {**PLAN['indicators'], 'ath': {'ndx': {'dd': -0.21}}}}
    plan = run(tmp_path / 'b', at(9, 41), Broker(cash=600_000), execute_orders=False, books=books, plan=fall)
    mine = {o['code']: o['qty'] for o in plan['orders']}
    assert mine == {'379810': 13, '0261H0': 5}  # 200,000 (40% of 500,000) as 140,000 : 60,000 at 10,010
    assert any('여운봉 단계 1' in n for n in plan['notes'])


def test_the_automatic_dca_time_waits_on_a_rising_day_in_the_live_account(tmp_path):
    # user 2026-11-18: 매수 시각 자동 — the quote (ask 10,010) above yesterday's close (9,000): no 적립식 order until 14:20
    plan = {**PLAN, 'indicators': {**PLAN['indicators'], 'krx_close': {k: 9_000 for k in L.ASSETS}}}
    broker = Broker(cash=2_000_000)
    run(tmp_path, at(9, 41), broker, execute_orders=False, plan=plan)
    cfg = {'on': True, 'amount_krw': 300_000, 'frequency': 'daily', 'time_mode': 'auto'}
    wait = run(tmp_path, at(10, 0), broker, execute_orders=False, plan=plan, dca=cfg)
    assert (wait['action'], wait['reason']) == ('WAIT', 'dca: automatic time waits for a price at or under yesterday close')
    assert run(tmp_path, at(14, 20), broker, execute_orders=False, plan=plan, dca=cfg)['action'] == 'DCA'


def test_a_yeo_account_s_dca_buys_its_pair_not_the_engine_etfs():
    # 2026-11-18 v2 (docs/longterm-dca-steady-plan.md 10-3 A): the 적립식 follows the account's strategy
    yeo = {'risk': 0.7}
    want = LL.dca_want({'info': {'want': {'NDX': 0.3, 'SPX': 0.3, 'SEMI': 0.1, 'RES': 0.3}}}, PLAN, yeo)
    assert want == pytest.approx({'NDX': 0.7, 'NDXMIX': 0.3})
    assert LL.dca_want({'info': {'want': {'NDX': 0.5, 'RES': 0.5}}}, PLAN) == {'NDX': 0.5}  # a module account: no KOFR


def test_automation_off_still_settles_the_executors_own_open_order_and_plans_nothing(tmp_path):
    broker = Broker()
    assert run(tmp_path, at(9, 41), broker, execute_orders=False, auto=False) == {'action': 'WAIT', 'reason': 'automation off'}
    assert broker.reads == 0 and not LL.load_state(tmp_path, 'live-2').get('orders')
    with patch.object(LL, 'open_order', return_value=True):  # an order it sent before the switch went off
        settled = run(tmp_path, at(9, 41), broker, execute_orders=False, auto=False, pending={'action': 'CANCEL', 'reason': 'stale'})
        idle = run(tmp_path, at(9, 42), broker, execute_orders=False, auto=False)
    assert settled['action'] == 'CANCEL' and idle['reason'] == 'automation off' and not LL.load_state(tmp_path, 'live-2').get('orders')


def test_a_yeo_steps_leg_that_was_refused_is_bought_the_next_day(tmp_path):
    # review 2026-11-18: the whole day's book was committed on the first send, so a refused leg of a step was never bought
    books = {'live-2': L.clean_book({'strategy': 'yeo_method'})}
    fall = {**PLAN, 'indicators': {**PLAN['indicators'], 'ath': {'ndx': {'dd': -0.21}}}}
    broker = Broker(cash=600_000)
    broker.refuse['0261H0'] = 'daily new symbol limit'  # a final refusal
    run(tmp_path, at(9, 41), broker, execute_orders=True, books=books, plan=fall)
    send_all(tmp_path, broker, books=books, plan=fall)
    assert LL.load_state(tmp_path, 'live-2')['book']['yeo']['ndx']['used'] == [-0.2]
    broker.held, broker.refuse = {'379810': 13}, {}
    with patch.object(LL, 'filled_on', side_effect=lambda root, key, day, side: {'379810': 13} if side == 'BUY' else {}):  # NDX leg filled
        nxt = run(tmp_path, datetime(2026, 11, 19, 9, 41, tzinfo=KST), broker, execute_orders=True, books=books,
                  plan={**fall, 'day': '2026-11-19'})
    assert {o['code']: o['qty'] for o in nxt['orders'] if o['side'] == 'BUY'}.get('0261H0') == 5
    state = LL.load_state(tmp_path, 'live-2')
    assert any('사지 못한' in n for n in nxt['notes']) and 'yeo_owed' not in state['book_next']  # paid once the plan is sent


def test_an_order_sent_before_a_crash_lost_its_save_is_not_sent_again(tmp_path):
    # review 2026-11-18: sent, then the process stopped before the state file was saved: the order still reads 'planned'
    from contextlib import closing
    broker = Broker(cash=600_000)
    run(tmp_path, at(9, 41), broker, execute_orders=True)
    first = next(o for o in LL.load_state(tmp_path, 'live-2')['orders'] if o['status'] == 'planned')
    g = guard(tmp_path)
    with closing(g._db()) as db, db:  # the guard's record of that send
        db.execute('INSERT INTO live_intents VALUES (?,?,?,?,?,?,?,?,?)', ('fp-lost', g.scope, '20261118',
                   'krstockOrderCashSell' if first['side'] == 'SELL' else 'krstockOrderCashBuy', first['code'], first['qty'], 9, 'terminal', first['qty']))
    result = run(tmp_path, at(9, 42), broker, execute_orders=True)
    assert result['execution'] == 'SENT_BEFORE_RESTART' and broker.sent == []
    state = LL.load_state(tmp_path, 'live-2')
    assert next(o for o in state['orders'] if o['code'] == first['code'])['status'] == 'sent' and 'book_next' not in state


def test_the_order_window_follows_the_exchange_clock_on_a_late_open_day():
    """수능 day 2026-11-19 opens at 10:00: no plan on pre-open quotes at 09:45."""
    from datetime import datetime
    import longterm_live
    import session_times; session_times.SHIFTED_DAYS = {"2026-11-19": 60}  # restored by conftest
    at = datetime(2026, 11, 19, 9, 45, tzinfo=longterm_live.KST)
    out = longterm_live.cycle(None, 'x', None, {}, 'live-2', now=at, execute_orders=False, may_cancel=False, root='/nonexistent')
    assert out == {'action': 'WAIT', 'reason': 'before 10:40'}
